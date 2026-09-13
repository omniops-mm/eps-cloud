"""Install a local EPS telemetry rehearsal using records from a successful CI run."""

import argparse
import json
import secrets
import shutil
import tempfile
from pathlib import Path

import yaml

from scripts.check_platform import PLATFORM, ROOT
from scripts.record_image_digest import validate_record
from scripts.rehearse_registry import CONTEXT, local_cluster, run
from scripts.rehearse_secret_controllers import verify_archive


def rehearsal_objects(raw):
    objects = [obj for obj in yaml.safe_load_all(raw) if obj and obj["kind"] != "Ingress"]
    for obj in objects:
        if obj["metadata"].get("namespace", "eps") != "eps":
            raise ValueError("Rehearsal resource escaped eps")
        obj["metadata"]["namespace"] = "eps"
        if obj["kind"] == "CronJob":
            obj["spec"]["suspend"] = True
    return objects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", type=Path)
    parser.add_argument(
        "--request-scaling",
        action="store_true",
        help="Enable bounded local request-rate scaling after adapter installation",
    )
    args = parser.parse_args()
    records = {}
    for path in args.records.glob("*.json"):
        record = json.loads(path.read_text())
        for name in ("web", "worker"):
            if record.get("image", "").startswith(f"ghcr.io/omniops-mm/eps-cloud/{name}@"):
                if name in records:
                    raise ValueError("Duplicate image record")
                records[name] = record
    if set(records) != {"web", "worker"}:
        raise ValueError("Both CI image records are required")
    source = records["web"]
    status = json.loads(
        run(["gh", "api", f"repos/omniops-mm/eps-cloud/actions/runs/{int(source['run_id'])}"])
    )
    if (
        status["conclusion"] != "success"
        or status["event"] != "push"
        or status["head_branch"] != "master"
        or status["path"] != ".github/workflows/ci.yml"
    ):
        raise ValueError("Expected a successful trusted master CI run")
    images = {
        name: validate_record(
            record,
            "omniops-mm/eps-cloud",
            name,
            status["head_sha"],
            str(status["id"]),
            str(status["run_attempt"]),
        )
        for name, record in records.items()
    }
    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("Helm is required on PATH")
    chart = ROOT / "deploy/helm/eps"
    values = {
        "image": {
            "webDigest": images["web"].split("@", 1)[1],
            "workerDigest": images["worker"].split("@", 1)[1],
        },
        "db": {"storage": "1Gi"},
        "web": {"replicas": 1},
        "hpa": {"enabled": False},
        "monitoring": {"enabled": True},
        "tracing": {"enabled": True, "sampleRate": 1.0},
    }
    if args.request_scaling:
        values["hpa"] = {
            "enabled": True,
            "minReplicas": 1,
            "maxReplicas": 3,
            "targetCPUPercent": 10000,
            "requestsPerSecond": 0.1,
            "scaleDownSeconds": 30,
        }
    objects = rehearsal_objects(
        run(
            [
                helm,
                "template",
                "eps",
                str(chart),
                "-n",
                "eps",
                "-f",
                str(chart / "values-production.yaml"),
                "-f",
                "-",
            ],
            yaml.safe_dump(values).encode(),
        )
    )
    with local_cluster() as command:
        if args.request_scaling:
            api = json.loads(
                run(command + ["get", "apiservice", "v1beta1.custom.metrics.k8s.io", "-o", "json"])
            )
            if not any(
                c.get("type") == "Available" and c.get("status") == "True"
                for c in api.get("status", {}).get("conditions", [])
            ):
                raise ValueError("The reviewed metrics adapter must be available")

        existing = json.loads(run(command + ["get", "secrets", "-n", "eps", "-o", "json"]))
        named = {obj["metadata"]["name"]: obj for obj in existing["items"]}
        required = {"eps", "eps-db-admin", "eps-exporter"}
        present = required & named.keys()
        if present and (
            present != required
            or any(
                named[n]["metadata"].get("labels", {}).get("eps.local/purpose") != "observability"
                for n in present
            )
        ):
            raise ValueError("Refusing to replace existing application credentials")
        if not present:
            resources = json.loads(
                run(
                    command
                    + [
                        "get",
                        "statefulsets,pvc,deployments,externalsecrets",
                        "-n",
                        "eps",
                        "-o",
                        "json",
                    ]
                )
            )
            if resources["items"]:
                raise ValueError("Fresh credentials require an unused application namespace")
            app, admin, exporter = (secrets.token_urlsafe(32) for _ in range(3))
            for name, data in {
                "eps": {
                    "POSTGRES_PASSWORD": app,
                    "DATABASE_URL": f"postgresql+psycopg://eps:{app}@db:5432/eps",
                    "SECRET_KEY": secrets.token_urlsafe(32),
                },
                "eps-db-admin": {"POSTGRES_PASSWORD": admin},
                "eps-exporter": {
                    "password": exporter,
                    "DATA_SOURCE_NAME": f"postgresql://eps_exporter:{exporter}@db:5432/eps?sslmode=disable",
                },
            }.items():
                obj = {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": name,
                        "namespace": "eps",
                        "labels": {"eps.local/purpose": "observability"},
                    },
                    "type": "Opaque",
                    "stringData": data,
                }
                run(command + ["create", "-f", "-"], json.dumps(obj).encode())
        # Install deny rules before workloads; no Ingress is created and jobs stay suspended.
        for group in (
            [o for o in objects if o["kind"] == "NetworkPolicy"],
            [o for o in objects if o["kind"] != "NetworkPolicy"],
        ):
            run(
                command + ["apply", "--server-side", "--field-manager=eps-local-app", "-f", "-"],
                json.dumps({"apiVersion": "v1", "kind": "List", "items": group}).encode(),
            )
        for target in ("statefulset/db", "deployment/web", "deployment/alert-log"):
            run(command + ["rollout", "status", target, "-n", "eps", "--timeout=150s"])
        print("PASS: local database, migrations, web and alert receiver ready", flush=True)
        run(
            command
            + [
                "apply",
                "--server-side",
                "--field-manager=eps-platform",
                "-f",
                str(PLATFORM / "exporter-networkpolicies.yaml"),
            ]
        )
        pin = json.loads((PLATFORM / "versions.json").read_text())["prometheus-postgres-exporter"]
        with tempfile.TemporaryDirectory(prefix="eps-exporter-") as temporary:
            run(
                [
                    helm,
                    "pull",
                    "prometheus-postgres-exporter",
                    "--repo",
                    pin["repository"],
                    "--version",
                    pin["version"],
                    "--destination",
                    temporary,
                ]
            )
            archive = Path(temporary) / f"prometheus-postgres-exporter-{pin['version']}.tgz"
            verify_archive(archive, pin["sha256"])
            run(
                [
                    helm,
                    "--kubeconfig",
                    command[2],
                    "--kube-context",
                    CONTEXT,
                    "upgrade",
                    "--install",
                    "postgres-exporter",
                    str(archive),
                    "-n",
                    "eps",
                    "-f",
                    str(PLATFORM / "prometheus-postgres-exporter-values.yaml"),
                    "--wait",
                    "--timeout",
                    "150s",
                ]
            )
        print("PASS: local PostgreSQL exporter ready; jobs suspended, no app ingress created")
    print("Test data and credentials retained locally. No Google resources changed.")


if __name__ == "__main__":
    main()
