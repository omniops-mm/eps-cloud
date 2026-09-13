"""Verify local scrape, log and trace correlation without printing payloads."""

import base64
import json
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


def main():
    with local_cluster() as command:
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
        check_network(command)
        check_alert_delivery(command)
    print("Temporary tunnels, probe pod and alert rule removed. Recovery checks are separate.")


if __name__ == "__main__":
    main()
