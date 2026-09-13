"""User-run local controller installation; no Google resources or secret payloads."""

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts.rehearse_registry import CONTEXT, local_cluster, run

PLATFORM = Path(__file__).resolve().parents[1] / "deploy/platform"


def verify_archive(path, expected):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Chart checksum mismatch")


def main():
    helm = shutil.which("helm") or str(
        Path.home() / "Desktop/LinuxDev/EPS-reference/EPS-v0.4-work/tools/bin/helm.exe"
    )
    if not Path(helm).is_file():
        raise RuntimeError("Existing Helm tool required")
    with tempfile.TemporaryDirectory(prefix="eps-controller-charts-") as temporary:
        charts = {}
        pins = json.loads((PLATFORM / "versions.json").read_text())
        for chart in ("cert-manager", "external-secrets"):
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
            charts[chart] = str(archive)
        with local_cluster() as command:
            if (
                run(
                    command
                    + [
                        "get",
                        "secret",
                        "dhi-pull",
                        "-n",
                        "external-secrets",
                        "-o",
                        "jsonpath={.type}",
                    ]
                )
                != b"kubernetes.io/dockerconfigjson"
            ):
                raise ValueError("Run registry rehearsal first")

            def apply_file(name, namespace=None):
                args = command + ["apply", "--server-side", "--field-manager=eps-platform"]
                if namespace:
                    args += ["-n", namespace]
                run(args + ["-f", str(PLATFORM / name)])

            def install(release, chart, namespace, values):
                args = [
                    helm,
                    "--kubeconfig",
                    command[2],
                    "--kube-context",
                    CONTEXT,
                    "upgrade",
                    "--install",
                    release,
                    charts[chart],
                    "--namespace",
                    namespace,
                    "--wait",
                    "--timeout",
                    "150s",
                ]
                for value in values:
                    args += ["-f", str(PLATFORM / value)]
                print(f"Installing {release} locally...", flush=True)
                run(args)

            for namespace, level in (("cert-manager", "restricted"), ("monitoring", "baseline")):
                obj = {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": namespace,
                        "labels": {
                            "pod-security.kubernetes.io/enforce": level,
                            "pod-security.kubernetes.io/enforce-version": "v1.36",
                        },
                    },
                }
                run(
                    command + ["apply", "--server-side", "--field-manager=eps-platform", "-f", "-"],
                    json.dumps(obj).encode(),
                )
            for namespace in ("cert-manager", "external-secrets", "monitoring"):
                apply_file("metadata-policy.yaml", namespace)
            install("cert-manager", "cert-manager", "cert-manager", ["cert-manager-values.yaml"])
            apply_file("eso-webhook-issuer.yaml")
            run(
                command
                + [
                    "wait",
                    "issuer/eso-webhook",
                    "-n",
                    "external-secrets",
                    "--for=condition=Ready",
                    "--timeout=120s",
                ]
            )
            apply_file("eso-identities.yaml")
            for namespace in ("eps", "monitoring"):
                install(
                    f"external-secrets-{namespace}",
                    "external-secrets",
                    "external-secrets",
                    ["external-secrets-values.yaml", f"external-secrets-{namespace}-values.yaml"],
                )
            print("PASS: cert-manager and ESO releases are ready")
            for namespace, other in (("eps", "monitoring"), ("monitoring", "eps")):
                for target, resource, name, expected in [
                    (namespace, "secrets", None, True),
                    (other, "secrets", None, False),
                    (namespace, "serviceaccounts", "eps-secrets", True),
                    (namespace, "serviceaccounts", "default", False),
                    (other, "serviceaccounts", "eps-secrets", False),
                ]:
                    attributes = {
                        "namespace": target,
                        "group": "",
                        "resource": resource,
                        "verb": "create" if name else "get",
                    }
                    if name:
                        attributes.update(name=name, subresource="token")
                    review = {
                        "apiVersion": "authorization.k8s.io/v1",
                        "kind": "SubjectAccessReview",
                        "spec": {
                            "user": "system:serviceaccount:external-secrets:"
                            f"external-secrets-{namespace}",
                            "groups": [
                                "system:serviceaccounts",
                                "system:serviceaccounts:external-secrets",
                                "system:authenticated",
                            ],
                            "resourceAttributes": attributes,
                        },
                    }
                    status = json.loads(
                        run(
                            command + ["create", "-f", "-", "-o", "json"],
                            json.dumps(review).encode(),
                        )
                    )["status"]
                    if status.get("evaluationError") or status.get("allowed") is not expected:
                        raise RuntimeError(f"Unexpected {namespace} controller permissions")
                print(f"PASS: {namespace} Secret and named-token permissions isolated")
    print("Controllers retained locally. Google federation remains unconfigured.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        print("CONTROLLER INSTALL FAILED: inspect local releases; credential output suppressed.")
        raise SystemExit(1) from None
