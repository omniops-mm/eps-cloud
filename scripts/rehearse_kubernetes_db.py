"""User-run Helm database/PVC rehearsal in an isolated, disposable namespace."""

import argparse
import base64
import json
import secrets
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from scripts.rehearse_registry import local_cluster, run

ROOT = Path(__file__).resolve().parents[1]


def decode_objects(raw):
    # kubectl emits adjacent JSON documents for multiple input YAML documents.
    remaining = raw.decode().strip()
    objects: list[dict] = []
    decoder = json.JSONDecoder()
    while remaining:
        obj, end = decoder.raw_decode(remaining)
        objects.extend(obj["items"] if obj.get("kind") == "List" else [obj])
        remaining = remaining[end:].lstrip()
    return objects


def database_objects(objects, namespace):
    expected = {("Service", "db"), ("StatefulSet", "db"), ("ConfigMap", "db-init")}
    if {(o["kind"], o["metadata"]["name"]) for o in objects} != expected or len(objects) != 3:
        raise ValueError("Expected only the rendered database and bootstrap resources")
    for obj in objects:
        if obj["metadata"].get("namespace", namespace) != namespace:
            raise ValueError("Rendered resource escaped the rehearsal namespace")
        obj["metadata"]["namespace"] = namespace
    return objects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--restore-source",
        action="store_true",
        help="Restore the local eps database into the isolated test PVC",
    )
    args = parser.parse_args()
    helm = shutil.which("helm") or str(
        Path.home() / "Desktop/LinuxDev/EPS-reference/EPS-v0.4-work/tools/bin/helm.exe"
    )
    namespace = "eps-db-check-" + uuid.uuid4().hex[:12]
    passwords = {key: secrets.token_urlsafe(32) for key in ("admin", "app", "exporter")}
    created = False
    with local_cluster() as command:

        def create(obj):
            run(command + ["create", "-f", "-"], json.dumps(obj).encode())

        def query(user, password, sql):
            # A private stdin line, never a password argument or logged manifest.
            return (
                run(
                    command
                    + [
                        "exec",
                        "-i",
                        "-n",
                        namespace,
                        "db-0",
                        "-c",
                        "postgres",
                        "--",
                        "/bin/sh",
                        "-c",
                        'IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql "$@"',
                        "query",
                        "-X",
                        "-w",
                        "-A",
                        "-t",
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        user,
                        "-d",
                        "eps",
                        "-c",
                        sql,
                    ],
                    (password + "\n").encode(),
                )
                .decode()
                .strip()
            )

        try:
            # A single existing credential is copied privately; never the whole kubeconfig/Docker config.
            registry = json.loads(
                run(command + ["get", "secret", "dhi-pull", "-n", "eps", "-o", "json"])
            )
            registry_auth = json.loads(base64.b64decode(registry["data"][".dockerconfigjson"]))[
                "auths"
            ]["dhi.io"]
            registry_data = base64.b64encode(
                json.dumps({"auths": {"dhi.io": registry_auth}}).encode()
            ).decode()
            if registry["type"] != "kubernetes.io/dockerconfigjson":
                raise ValueError("Expected a registry credential in eps/dhi-pull")
            create(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": namespace,
                        "labels": {
                            "eps.rehearsal": "database",
                            "pod-security.kubernetes.io/enforce": "restricted",
                            "pod-security.kubernetes.io/enforce-version": "v1.36",
                        },
                    },
                }
            )
            created = True
            create(
                {
                    "apiVersion": "networking.k8s.io/v1",
                    "kind": "NetworkPolicy",
                    "metadata": {"name": "default-deny", "namespace": namespace},
                    "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
                }
            )
            create(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": "dhi-pull", "namespace": namespace},
                    "type": registry["type"],
                    "data": {".dockerconfigjson": registry_data},
                }
            )
            for name, fields in {
                "eps-db-admin": {"POSTGRES_PASSWORD": passwords["admin"]},
                "eps": {"POSTGRES_PASSWORD": passwords["app"]},
                "eps-exporter": {"password": passwords["exporter"]},
            }.items():
                create(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": name, "namespace": namespace},
                        "data": {
                            key: base64.b64encode(value.encode()).decode()
                            for key, value in fields.items()
                        },
                    }
                )
            chart = ROOT / "deploy/helm/eps"
            rendered = run(
                [
                    helm,
                    "template",
                    "eps",
                    str(chart),
                    "--namespace",
                    namespace,
                    "-f",
                    str(chart / "values-production.yaml"),
                    "--set",
                    "db.storage=1Gi",
                    "--show-only",
                    "templates/postgres.yaml",
                    "--show-only",
                    "templates/db-init.yaml",
                ]
            )
            converted = decode_objects(
                run(
                    command
                    + [
                        "-n",
                        namespace,
                        "create",
                        "--dry-run=client",
                        "--validate=false",
                        "-f",
                        "-",
                        "-o",
                        "json",
                    ],
                    rendered,
                )
            )
            objects = database_objects(converted, namespace)
            create({"apiVersion": "v1", "kind": "List", "items": objects})
            run(
                command + ["rollout", "status", "statefulset/db", "-n", namespace, "--timeout=150s"]
            )
            print("PASS: Helm database init container, restricted pod and fresh PVC are ready")
            roles = query(
                "eps_admin",
                passwords["admin"],
                "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname IN ('eps','eps_exporter') ORDER BY rolname",
            )
            if roles.splitlines() != ["eps|f|f|f|f", "eps_exporter|f|f|f|f"]:
                raise ValueError("Unexpected database role privileges")
            if (
                query(
                    "eps_exporter",
                    passwords["exporter"],
                    "SELECT pg_has_role(current_user,'pg_monitor','member')",
                )
                != "t"
            ):
                raise ValueError("Exporter monitoring grant missing")
            if args.restore_source:
                backup = run(
                    command
                    + [
                        "exec",
                        "-n",
                        "eps",
                        "db-0",
                        "-c",
                        "postgres",
                        "--",
                        "/bin/sh",
                        "-ec",
                        'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_dump -h 127.0.0.1 -U eps_admin -d eps --format=custom --no-owner --no-acl',
                    ]
                )
                if not backup.startswith(b"PGDMP"):
                    raise ValueError("Expected a PostgreSQL custom archive")
                run(
                    command
                    + [
                        "exec",
                        "-i",
                        "-n",
                        namespace,
                        "db-0",
                        "-c",
                        "postgres",
                        "--",
                        "/bin/sh",
                        "-ec",
                        "IFS= read -r PGPASSWORD; export PGPASSWORD; exec pg_restore -h 127.0.0.1 -U eps -d eps --exit-on-error --no-owner --no-acl",
                    ],
                    passwords["app"].encode() + b"\n" + backup,
                )
                del backup
                source_version = (
                    run(
                        command
                        + [
                            "exec",
                            "-n",
                            "eps",
                            "db-0",
                            "-c",
                            "postgres",
                            "--",
                            "/bin/sh",
                            "-ec",
                            'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -X -w -A -t -h 127.0.0.1 -U eps_admin -d eps -c "SELECT version_num FROM alembic_version"',
                        ]
                    )
                    .decode()
                    .strip()
                )
                if (
                    not source_version
                    or query("eps", passwords["app"], "SELECT version_num FROM alembic_version")
                    != source_version
                ):
                    raise ValueError("Restored migration version differs from source")
                print(
                    "PASS: source database archive crossed the host and restored into an isolated PVC; migration version matches",
                    flush=True,
                )
            query(
                "eps",
                passwords["app"],
                "CREATE TABLE rehearsal_canary (value integer); INSERT INTO rehearsal_canary VALUES (1)",
            )
            print("PASS: application and exporter authenticate with the expected privileges")
            previous = passwords["app"]
            replacement = secrets.token_urlsafe(32)
            sql = "SET log_statement='none'; SET log_min_error_statement='panic';\n\\getenv replacement NEW_PASSWORD\nSELECT format('ALTER ROLE eps PASSWORD %L', :'replacement') \\gexec\n"
            run(
                command
                + [
                    "exec",
                    "-i",
                    "-n",
                    namespace,
                    "db-0",
                    "-c",
                    "postgres",
                    "--",
                    "/bin/sh",
                    "-ec",
                    "IFS= read -r PGPASSWORD; IFS= read -r NEW_PASSWORD; export PGPASSWORD NEW_PASSWORD; exec psql -X -q -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -U eps_admin -d eps",
                ],
                (passwords["admin"] + "\n" + replacement + "\n" + sql).encode(),
            )
            if query("eps", replacement, "SELECT 1") != "1":
                raise ValueError("New database password failed")
            try:
                query("eps", previous, "SELECT 1")
            except RuntimeError:
                pass
            else:
                raise ValueError("Old database password still works")
            secret = json.loads(
                run(command + ["get", "secret", "eps", "-n", namespace, "-o", "json"])
            )
            secret["data"]["POSTGRES_PASSWORD"] = base64.b64encode(replacement.encode()).decode()
            run(command + ["replace", "-f", "-"], json.dumps(secret).encode())
            passwords["app"] = replacement
            print(
                "PASS: isolated password rotation accepts new credentials and rejects old credentials",
                flush=True,
            )
            old = json.loads(run(command + ["get", "pod", "db-0", "-n", namespace, "-o", "json"]))[
                "metadata"
            ]["uid"]
            run(command + ["delete", "pod", "db-0", "-n", namespace, "--wait=true"])
            for _ in range(75):
                raw = run(
                    command
                    + ["get", "pod", "db-0", "-n", namespace, "--ignore-not-found", "-o", "json"]
                )
                pod = json.loads(raw) if raw.strip() else {}
                if pod.get("metadata", {}).get("uid") not in (None, old) and any(
                    c.get("type") == "Ready" and c.get("status") == "True"
                    for c in pod.get("status", {}).get("conditions", [])
                ):
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Replacement database pod did not become ready")
            if query("eps", passwords["app"], "SELECT value FROM rehearsal_canary") != "1":
                raise ValueError("Data did not survive pod replacement")
            print("PASS: pod replacement reruns bootstrap safely and preserves database data")
        finally:
            if created:
                run(command + ["delete", "namespace", namespace, "--wait=true", "--timeout=150s"])
                print(
                    "Disposable namespace, test credentials and PVC removed; existing EPS data untouched"
                )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        print(f"KUBERNETES DATABASE REHEARSAL FAILED: {error}")
        raise SystemExit(1) from None
