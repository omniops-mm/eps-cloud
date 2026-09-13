"""Verify local scrape, log and trace correlation without printing payloads."""

import argparse
import base64
import json
import secrets
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager

import requests

from scripts.rehearse_registry import local_cluster, run


@contextmanager
def forward(command, namespace, service, target):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        command
        + [
            "port-forward",
            "-n",
            namespace,
            f"service/{service}",
            f"{port}:{target}",
            "--address=127.0.0.1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        for _ in range(50):
            if process.poll() is not None:
                raise RuntimeError("Local port forward failed")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Local port forward did not become ready")
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=10)


def check_network(command):
    image = json.loads(run(command + ["get", "deployment", "web", "-n", "eps", "-o", "json"]))[
        "spec"
    ]["template"]["spec"]["containers"][0]["image"]
    name = "eps-network-check-" + uuid.uuid4().hex[:10]
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": "monitoring"},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "activeDeadlineSeconds": 120,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "probe",
                    "image": image,
                    "command": ["python", "-c", "import time; time.sleep(90)"],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "24Mi"},
                        "limits": {"cpu": "100m", "memory": "64Mi"},
                    },
                }
            ],
        },
    }
    run(command + ["create", "-f", "-"], json.dumps(pod).encode())
    try:
        run(
            command
            + ["wait", "-n", "monitoring", "pod/" + name, "--for=condition=Ready", "--timeout=60s"]
        )
        code = """import socket
for host,port in [('loki.monitoring.svc',3100),('tempo.monitoring.svc',3200),('tempo.monitoring.svc',4318),('db.eps.svc',5432),('web.eps.svc',8000)]:
    try:
        connection=socket.create_connection((host,port),timeout=2)
    except (TimeoutError, ConnectionRefusedError):
        continue
    else:
        connection.close()
        raise RuntimeError('Unapproved client reached protected service')
print('PASS')
"""
        if (
            run(
                command + ["exec", "-i", "-n", "monitoring", name, "--", "python", "-"],
                code.encode(),
            ).strip()
            != b"PASS"
        ):
            raise ValueError("Network denial check did not complete")
        print(
            "PASS: unapproved pod denied Loki, Tempo ingestion/query, database and web access",
            flush=True,
        )
    finally:
        run(command + ["delete", "pod", name, "-n", "monitoring", "--wait=false"])


def check_alert_delivery(command):
    name = "eps-rehearsal-" + uuid.uuid4().hex[:10]
    rule: dict = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {"name": name, "namespace": "eps", "labels": {"release": "monitoring"}},
        "spec": {
            "groups": [
                {
                    "name": name,
                    "rules": [
                        {
                            "alert": name,
                            "expr": "vector(1)",
                            "labels": {"namespace": "eps", "severity": "warning"},
                        }
                    ],
                }
            ]
        },
    }
    run(command + ["create", "-f", "-"], json.dumps(rule).encode())
    try:
        for status in ("firing", "resolved"):
            for _ in range(60):
                raw = run(
                    command + ["logs", "-n", "eps", "deployment/alert-log", "--since=5m"]
                ).decode()
                entries = [json.loads(line) for line in raw.splitlines() if line.startswith("{")]
                if any(
                    e.get("labels", {}).get("alertname") == name and e.get("status") == status
                    for e in entries
                ):
                    print(
                        f"PASS: Prometheus rule reached the alert receiver as {status}", flush=True
                    )
                    break
                time.sleep(2)
            else:
                raise RuntimeError(f"Alert {status} delivery timed out")
            if status == "firing":
                run(
                    command
                    + [
                        "patch",
                        "prometheusrule",
                        name,
                        "-n",
                        "eps",
                        "--type=json",
                        "-p",
                        json.dumps(
                            [
                                {
                                    "op": "replace",
                                    "path": "/spec/groups/0/rules/0/expr",
                                    "value": "vector(0) > 1",
                                }
                            ]
                        ),
                    ]
                )
    finally:
        run(command + ["delete", "prometheusrule", name, "-n", "eps"])


def replace_stateful_pod(command, namespace, name):
    """Replace one pod and require its persistent-volume identities to remain unchanged."""

    def snapshot():
        pod = json.loads(run(command + ["get", "pod", name + "-0", "-n", namespace, "-o", "json"]))
        claims = [
            v["persistentVolumeClaim"]["claimName"]
            for v in pod["spec"]["volumes"]
            if "persistentVolumeClaim" in v
        ]
        if not claims or not any(
            o.get("kind") == "StatefulSet" and o.get("name") == name
            for o in pod["metadata"].get("ownerReferences", [])
        ):
            raise ValueError("Expected a persistent StatefulSet pod")
        volumes = {
            claim: json.loads(run(command + ["get", "pvc", claim, "-n", namespace, "-o", "json"]))[
                "metadata"
            ]["uid"]
            for claim in claims
        }
        return pod["metadata"]["uid"], volumes

    before, volumes = snapshot()
    run(command + ["rollout", "restart", "statefulset/" + name, "-n", namespace])
    run(command + ["rollout", "status", "statefulset/" + name, "-n", namespace, "--timeout=150s"])
    after, retained = snapshot()
    if before == after or volumes != retained:
        raise ValueError("Pod replacement or volume retention check failed")
    print(f"PASS: {name} replacement ready with the same PVC identities", flush=True)


def check_database_restart(command):
    table = "eps_recovery_" + uuid.uuid4().hex

    def query(sql):
        code = (
            "import os,psycopg; c=psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg:', 'postgresql:')); c.autocommit=True; r=c.execute("
            + repr(sql)
            + "); print(r.fetchone()[0] if r.description else 'OK'); c.close()"
        )
        return run(
            command + ["exec", "-n", "eps", "deployment/web", "--", "python", "-c", code]
        ).strip()

    query(f"CREATE TABLE {table} (value integer); INSERT INTO {table} VALUES (42)")
    try:
        replace_stateful_pod(command, "eps", "db")
        if query(f"SELECT value FROM {table}") != b"42":
            raise ValueError("Database canary did not survive replacement")
        print("PASS: database canary survived replacement", flush=True)
    finally:
        query(f"DROP TABLE IF EXISTS {table}")


def check_maintenance(command):
    secret = json.loads(run(command + ["get", "secret", "eps", "-n", "eps", "-o", "json"]))
    if secret["metadata"].get("labels", {}).get("eps.local/purpose") != "observability":
        raise ValueError("Signing-key drill requires owned local test credentials")
    if json.loads(run(command + ["get", "externalsecrets", "-n", "eps", "-o", "json"]))["items"]:
        raise ValueError("Refusing to rotate externally managed credentials")
    code = "from app import create_app; from flask.sessions import SecureCookieSessionInterface; a=create_app(); print(SecureCookieSessionInterface().get_signing_serializer(a).dumps({'test':42}))"
    cookie = run(
        command + ["exec", "-n", "eps", "deployment/web", "--", "python", "-c", code]
    ).strip()
    secret["data"]["SECRET_KEY"] = base64.b64encode(secrets.token_urlsafe(32).encode()).decode()
    run(command + ["replace", "-f", "-"], json.dumps(secret).encode())
    run(command + ["rollout", "restart", "deployment/web", "-n", "eps"])
    run(command + ["rollout", "status", "deployment/web", "-n", "eps", "--timeout=150s"])
    code = """import sys
from app import create_app
from flask.sessions import SecureCookieSessionInterface
from itsdangerous import BadSignature
signer=SecureCookieSessionInterface().get_signing_serializer(create_app())
try: signer.loads(sys.stdin.read().strip())
except BadSignature: pass
else: raise RuntimeError('Old signing key remains valid')
assert signer.loads(signer.dumps({'test':42})) == {'test':42}
print('PASS')
"""
    if (
        run(
            command + ["exec", "-i", "-n", "eps", "deployment/web", "--", "python", "-c", code],
            cookie,
        ).strip()
        != b"PASS"
    ):
        raise ValueError("Signing-key rotation check failed")
    print(
        "PASS: old signed state rejected and new signed state accepted after local key rotation",
        flush=True,
    )
    jobs = json.loads(run(command + ["get", "jobs", "-n", "eps", "-o", "json"]))["items"]
    if any(j.get("status", {}).get("active", 0) for j in jobs):
        raise ValueError("Wait for active jobs before catch-up checks")
    for source in ("cleanup-audit-log", "refresh-weather"):
        name = "eps-catchup-" + uuid.uuid4().hex[:12]
        run(command + ["create", "job", name, "-n", "eps", "--from=cronjob/" + source])
        try:
            run(
                command
                + ["wait", "job/" + name, "-n", "eps", "--for=condition=Complete", "--timeout=150s"]
            )
            print(f"PASS: explicit {source} catch-up completed", flush=True)
        finally:
            run(command + ["delete", "job", name, "-n", "eps"])


def check_scaling(command):
    def hpa():
        return json.loads(run(command + ["get", "hpa", "web", "-n", "eps", "-o", "json"]))

    config = hpa()["spec"]
    if (
        config["minReplicas"] != 1
        or config["maxReplicas"] != 3
        or config["metrics"][0]["resource"]["target"]["averageUtilization"] != 10000
    ):
        raise ValueError("Use the bounded request-scaling rehearsal settings first")
    session = requests.Session()
    session.trust_env = False
    with forward(command, "eps", "web", 8000) as web:
        for _ in range(120):
            response = session.get(web + "/", timeout=5, allow_redirects=False)
            if response.status_code != 200:
                raise RuntimeError("Read-only scaling traffic failed")
            time.sleep(0.5)
    for _ in range(45):
        state = hpa()["status"]
        if state.get("currentReplicas", 0) >= 2 and state.get("desiredReplicas", 0) >= 2:
            print(
                "PASS: request metric scaled web above one replica with CPU target deliberately unreachable",
                flush=True,
            )
            break
        time.sleep(2)
    else:
        raise RuntimeError("Request-driven scale-up was not observed")
    run(command + ["scale", "deployment/prometheus-adapter", "-n", "monitoring", "--replicas=0"])
    try:
        run(
            command
            + [
                "wait",
                "apiservice/v1beta1.custom.metrics.k8s.io",
                "--for=condition=Available=false",
                "--timeout=60s",
            ]
        )
        result = subprocess.run(
            command
            + [
                "get",
                "--raw=/apis/custom.metrics.k8s.io/v1beta1/namespaces/eps/pods/*/eps_http_requests_per_second",
            ],
            capture_output=True,
            check=False,
            timeout=15,
        )
        if result.returncode == 0:
            raise ValueError("Missing metrics unexpectedly returned a successful response")
        print(
            "PASS: unavailable request metrics return an API error rather than zero load",
            flush=True,
        )
    finally:
        run(
            command + ["scale", "deployment/prometheus-adapter", "-n", "monitoring", "--replicas=1"]
        )
        run(
            command
            + [
                "rollout",
                "status",
                "deployment/prometheus-adapter",
                "-n",
                "monitoring",
                "--timeout=150s",
            ]
        )
    for _ in range(90):
        if hpa()["status"].get("currentReplicas") == 1:
            print("PASS: adapter recovered and web downscaled after traffic stopped", flush=True)
            return
        time.sleep(2)
    raise RuntimeError("Bounded local downscale was not observed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Replace local stateful pods and verify retained data",
    )
    parser.add_argument(
        "--maintenance",
        action="store_true",
        help="Rotate the owned local signing key and run bounded catch-up jobs",
    )
    parser.add_argument(
        "--scaling",
        action="store_true",
        help="Run bounded read-only load and adapter outage/recovery checks",
    )
    args = parser.parse_args()
    with local_cluster() as command:
        if args.scaling:
            check_scaling(command)
            return
        if args.maintenance:
            check_maintenance(command)
            return
        session = requests.Session()
        session.trust_env = False
        marker = "private-" + uuid.uuid4().hex
        request_id = uuid.uuid4().hex
        with forward(command, "eps", "web", 8000) as web:
            response = session.get(
                web + "/",
                params={"private": marker},
                headers={"X-Request-ID": request_id, "Authorization": "Bearer " + marker},
                timeout=10,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise RuntimeError("Local app request failed")
            metrics = session.get(
                web + "/metrics",
                headers={"Accept": "application/openmetrics-text; version=1.0.0"},
                timeout=5,
            ).text
            if marker in metrics or "trace_id=" not in metrics:
                raise ValueError("Missing metric exemplar or unexpected private marker")
        secret = json.loads(
            run(command + ["get", "secret", "eps-grafana", "-n", "monitoring", "-o", "json"])
        )["data"]
        session.auth = (
            base64.b64decode(secret["admin-user"]).decode(),
            base64.b64decode(secret["admin-password"]).decode(),
        )
        with forward(command, "monitoring", "grafana", 80) as grafana:

            def get(uid, path, params=None):
                response = session.get(
                    f"{grafana}/api/datasources/proxy/uid/{uid}{path}",
                    params=params,
                    timeout=10,
                    allow_redirects=False,
                )
                response.raise_for_status()
                return response.json()

            for _ in range(30):
                targets = get(
                    "prometheus",
                    "/api/v1/query",
                    {"query": 'up{namespace="eps",service=~"web|postgres-exporter"}'},
                )["data"]["result"]
                if {r["metric"]["service"] for r in targets if r["value"][1] == "1"} == {
                    "web",
                    "postgres-exporter",
                }:
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Expected healthy web and PostgreSQL scrape targets")
            pg = get("prometheus", "/api/v1/query", {"query": 'pg_up{namespace="eps"}'})["data"][
                "result"
            ]
            if not pg or any(r["value"][1] != "1" for r in pg):
                raise RuntimeError("PostgreSQL exporter cannot query the database")
            print(
                "PASS: live web and database scrapes, database exporter authentication and OpenMetrics exemplars",
                flush=True,
            )
            for _ in range(30):
                logs = get(
                    "loki",
                    "/loki/api/v1/query_range",
                    {"query": '{namespace="eps",app="web"} |= "' + request_id + '"', "limit": 100},
                )["data"]["result"]
                entries = [json.loads(line) for stream in logs for _, line in stream["values"]]
                matching = [entry for entry in entries if entry.get("request_id") == request_id]
                if matching:
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Request log did not reach Loki")
            if marker in json.dumps(logs):
                raise ValueError("Private request marker appeared in Loki")
            trace_id = matching[0]["trace_id"]
            trace = None
            for _ in range(30):
                try:
                    trace = get("tempo", f"/api/traces/{trace_id}")
                    break
                except requests.HTTPError as error:
                    if error.response is None or error.response.status_code != 404:
                        raise
                    time.sleep(2)
            if not trace or marker in json.dumps(trace):
                raise ValueError("Trace missing or private marker appeared in Tempo")
            print(
                "PASS: actual app request reaches Loki and its trace is retrievable from Tempo; query/header marker absent",
                flush=True,
            )
            if args.restart:
                historical_time = time.time() - 30
                parameters = {"query": 'pg_up{namespace="eps"}', "time": historical_time}
                historical = get("prometheus", "/api/v1/query", parameters)["data"]["result"]
                if not historical:
                    raise ValueError("Historical metric sample is required")
                for name in ("loki", "tempo", "prometheus-monitoring-prometheus"):
                    replace_stateful_pod(command, "monitoring", name)
                recovered_logs = get(
                    "loki",
                    "/loki/api/v1/query_range",
                    {"query": '{namespace="eps",app="web"} |= "' + request_id + '"', "limit": 100},
                )["data"]["result"]
                if not recovered_logs or not get("tempo", f"/api/traces/{trace_id}"):
                    raise ValueError("Pre-restart telemetry was not recovered")
                if get("prometheus", "/api/v1/query", parameters)["data"]["result"] != historical:
                    raise ValueError("Historical metrics changed across replacement")
                print(
                    "PASS: pre-restart log, trace and historical metric sample recovered",
                    flush=True,
                )
        if args.restart:
            check_database_restart(command)
        else:
            check_network(command)
            check_alert_delivery(command)
    print("Temporary resources removed; local workloads and volumes retained.")


if __name__ == "__main__":
    main()
