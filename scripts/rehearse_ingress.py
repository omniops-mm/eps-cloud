"""Install and verify the local private HTTPS ingress and blackbox probe."""

import base64
import json
import shutil
import tempfile
from pathlib import Path

from scripts.check_platform import PLATFORM, ROOT
from scripts.rehearse_registry import CONTEXT, local_cluster, run
from scripts.rehearse_secret_controllers import verify_archive


def main():
    ports = json.loads(
        run(
            [
                "docker",
                "inspect",
                "k3d-eps-v04-dev-serverlb",
                "--format",
                "{{json .NetworkSettings.Ports}} ",
            ]
        )
    )
    if not ports or any(
        binding["HostIp"] != "127.0.0.1"
        for bindings in ports.values()
        for binding in (bindings or [])
    ):
        raise ValueError("Local ingress ports must bind only to loopback")
    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("Helm is required")
    with local_cluster() as command:
        current = run(
            command + ["get", "clusterissuer", "selfsigned", "--ignore-not-found", "-o", "json"]
        )
        if current.strip() and json.loads(current)["spec"] != {"ca": {"secretName": "eps-root-ca"}}:
            raise ValueError("Refusing to replace an unrelated issuer")
        run(
            command
            + [
                "apply",
                "--server-side",
                "--field-manager=eps-platform",
                "-f",
                str(PLATFORM / "private-issuer.yaml"),
            ]
        )
        run(
            command
            + [
                "wait",
                "certificate/eps-root-ca",
                "-n",
                "cert-manager",
                "--for=condition=Ready",
                "--timeout=120s",
            ]
        )
        run(
            command
            + ["wait", "clusterissuer/selfsigned", "--for=condition=Ready", "--timeout=120s"]
        )
        # Copy only the public trust anchor; the root private key stays in cert-manager.
        root = json.loads(
            run(command + ["get", "secret", "eps-root-ca", "-n", "cert-manager", "-o", "json"])
        )
        ca = base64.b64decode(root["data"]["tls.crt"]).decode()
        run(
            command + ["apply", "--server-side", "--field-manager=eps-platform", "-f", "-"],
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": "eps-ingress-ca", "namespace": "monitoring"},
                    "data": {"ca.crt": ca},
                }
            ).encode(),
        )
        ingress = run(
            [
                helm,
                "template",
                "eps",
                str(ROOT / "deploy/helm/eps"),
                "-n",
                "eps",
                "-f",
                str(ROOT / "deploy/helm/eps/values-production.yaml"),
                "--show-only",
                "templates/ingress.yaml",
            ]
        )
        run(
            command
            + ["apply", "--server-side", "--field-manager=eps-local-app", "-n", "eps", "-f", "-"],
            ingress,
        )
        run(
            command
            + [
                "wait",
                "certificate/eps-tls",
                "-n",
                "eps",
                "--for=condition=Ready",
                "--timeout=120s",
            ]
        )
        run(
            command
            + [
                "apply",
                "--server-side",
                "--field-manager=eps-platform",
                "-f",
                str(PLATFORM / "blackbox-network.yaml"),
            ]
        )
        pin = json.loads((PLATFORM / "versions.json").read_text())["prometheus-blackbox-exporter"]
        with tempfile.TemporaryDirectory(prefix="eps-blackbox-") as temporary:
            run(
                [
                    helm,
                    "pull",
                    "prometheus-blackbox-exporter",
                    "--repo",
                    pin["repository"],
                    "--version",
                    pin["version"],
                    "--destination",
                    temporary,
                ]
            )
            archive = Path(temporary) / f"prometheus-blackbox-exporter-{pin['version']}.tgz"
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
                    "blackbox",
                    str(archive),
                    "-n",
                    "monitoring",
                    "-f",
                    str(PLATFORM / "prometheus-blackbox-exporter-values.yaml"),
                    "--wait",
                    "--timeout",
                    "150s",
                ]
            )
        print(
            "PASS: private CA, local ingress certificate and blackbox deployment ready; TLS verification enabled"
        )


if __name__ == "__main__":
    main()
