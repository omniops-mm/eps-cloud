"""Install the pinned monitoring stack in the dedicated local cluster only."""

import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts.check_platform import PLATFORM, dashboard_configmap
from scripts.rehearse_registry import CONTEXT, local_cluster, registry_auth, run
from scripts.rehearse_secret_controllers import verify_archive


def main():
    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("Helm is required on PATH")
    pins = json.loads((PLATFORM / "versions.json").read_text())
    charts = ("kube-prometheus-stack", "loki", "tempo", "alloy", "prometheus-adapter")
    with tempfile.TemporaryDirectory(prefix="eps-monitoring-") as temporary:
        archives = {}
        for chart in charts:
            pin = pins[chart]
            run(
                [
                    helm,
                    "pull",
                    chart,
                    "--repo",
                    pin["repository"],
                    "--version",
                    pin["version"],
                    "--destination",
                    temporary,
                ]
            )
            archive = Path(temporary) / f"{chart}-{pin['version']}.tgz"
            verify_archive(archive, pin["sha256"])
            archives[chart] = str(archive)
        with local_cluster() as command:

            def apply(obj):
                run(
                    command
                    + ["apply", "--server-side", "--field-manager=eps-monitoring", "-f", "-"],
                    json.dumps(obj).encode(),
                )

            # Existing credentials are retained. New values travel only through stdin.
            existing = json.loads(
                run(command + ["get", "secrets", "-n", "monitoring", "-o", "json"])
            )
            names = {obj["metadata"]["name"]: obj for obj in existing["items"]}
            if "dhi-pull" not in names:
                config = (
                    Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")))
                    / "config.json"
                )
                auth = registry_auth(json.loads(config.read_text(encoding="utf-8")))
                apply(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "dhi-pull", "namespace": "monitoring"},
                        "type": "kubernetes.io/dockerconfigjson",
                        "stringData": {
                            ".dockerconfigjson": json.dumps({"auths": {"dhi.io": {"auth": auth}}})
                        },
                    }
                )
            elif names["dhi-pull"].get("type") != "kubernetes.io/dockerconfigjson":
                raise ValueError("Unexpected registry Secret type")
            if "eps-grafana" not in names:
                apply(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {
                            "name": "eps-grafana",
                            "namespace": "monitoring",
                            "labels": {"eps.local/purpose": "monitoring-rehearsal"},
                        },
                        "type": "Opaque",
                        "stringData": {
                            "admin-user": "admin",
                            "admin-password": secrets.token_urlsafe(32),
                        },
                    }
                )
            elif (
                not {"admin-user", "admin-password"} <= names["eps-grafana"].get("data", {}).keys()
            ):
                raise ValueError("Grafana Secret fields are missing")
            apply(dashboard_configmap())
            for filename in ("metadata-policy.yaml", "telemetry-network.yaml"):
                run(
                    command
                    + [
                        "apply",
                        "--server-side",
                        "--field-manager=eps-platform",
                        "-n",
                        "monitoring",
                        "-f",
                        str(PLATFORM / filename),
                    ]
                )
            for chart in charts:
                pin = pins[chart]
                print(f"Installing {pin['release']} locally...", flush=True)
                run(
                    [
                        helm,
                        "--kubeconfig",
                        command[2],
                        "--kube-context",
                        CONTEXT,
                        "upgrade",
                        "--install",
                        pin["release"],
                        archives[chart],
                        "--namespace",
                        "monitoring",
                        "--wait",
                        "--timeout",
                        "150s",
                        "-f",
                        str(PLATFORM / f"{chart}-values.yaml"),
                    ]
                )
            workloads = json.loads(
                run(command + ["get", "deployments,statefulsets", "-n", "monitoring", "-o", "json"])
            )
            for obj in workloads["items"]:
                if obj.get("status", {}).get("readyReplicas", 0) != obj["spec"].get("replicas", 1):
                    raise RuntimeError("Monitoring workload is not ready")
            grafana = json.loads(
                run(command + ["get", "deployment", "grafana", "-n", "monitoring", "-o", "json"])
            )
            pod = grafana["spec"]["template"]["spec"]
            if (
                pod.get("automountServiceAccountToken") is not False
                or len(pod["containers"]) != 1
                or pod.get("initContainers")
            ):
                raise ValueError("Unexpected Grafana token or extra container")
            expected = dashboard_configmap()
            actual = json.loads(
                run(
                    command
                    + [
                        "get",
                        "configmap",
                        expected["metadata"]["name"],
                        "-n",
                        "monitoring",
                        "-o",
                        "json",
                    ]
                )
            )
            if actual.get("data") != expected["data"]:
                raise ValueError("Dashboard ConfigMap differs from source")
            volume = next(v for v in pod["volumes"] if v["name"] == "dashboards-default")
            mount = next(
                v for v in pod["containers"][0]["volumeMounts"] if v["name"] == "dashboards-default"
            )
            if (
                volume.get("configMap", {}).get("name") != expected["metadata"]["name"]
                or mount.get("mountPath") != "/var/lib/grafana/dashboards/default"
            ):
                raise ValueError("Unexpected dashboard mount")
            print(
                "PASS: all local monitoring workloads ready; Grafana mounts the three source dashboards without an API token"
            )
    print(
        "Local releases, PVCs and credentials retained. No cloud resources changed. Ingestion and network tests remain separate."
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        print(
            "MONITORING INSTALL FAILED: inspect local workload status; credential output suppressed."
        )
        raise SystemExit(1) from None
