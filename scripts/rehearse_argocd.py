"""Install Argo in the existing private local cluster using preloaded images."""

import base64
import json
import secrets
import shutil
import tempfile
from pathlib import Path

from scripts.rehearse_registry import CONTEXT, local_cluster, run
from scripts.rehearse_secret_controllers import verify_archive

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "deploy/platform"


def main():
    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("Helm is required")
    pin = json.loads((PLATFORM / "pending/charts.json").read_text())["argo-cd"]
    with tempfile.TemporaryDirectory(prefix="eps-argo-") as temporary:
        run(
            [
                helm,
                "pull",
                "argo-cd",
                "--repo",
                pin["repository"],
                "--version",
                pin["version"],
                "--destination",
                temporary,
            ]
        )
        archive = Path(temporary) / f"argo-cd-{pin['version']}.tgz"
        verify_archive(archive, pin["digest"])
        with local_cluster() as command:

            def apply(obj):
                run(
                    command + ["apply", "--server-side", "--field-manager=eps-argo", "-f", "-"],
                    json.dumps(obj).encode(),
                )

            apply(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": "argocd",
                        "labels": {
                            "pod-security.kubernetes.io/enforce": "restricted",
                            "pod-security.kubernetes.io/enforce-version": "v1.36",
                        },
                    },
                }
            )
            endpoints = json.loads(
                run(command + ["get", "endpoints", "kubernetes", "-n", "default", "-o", "json"])
            )
            api_peers = [
                {"ipBlock": {"cidr": address["ip"] + "/32"}}
                for subset in endpoints["subsets"]
                for address in subset["addresses"]
            ]
            apply(
                {
                    "apiVersion": "networking.k8s.io/v1",
                    "kind": "NetworkPolicy",
                    "metadata": {"name": "private-argo", "namespace": "argocd"},
                    "spec": {
                        "podSelector": {},
                        "policyTypes": ["Ingress", "Egress"],
                        "ingress": [{"from": [{"podSelector": {}}]}],
                        "egress": [
                            {"to": [{"podSelector": {}}]},
                            {
                                "to": [
                                    {
                                        "namespaceSelector": {
                                            "matchLabels": {
                                                "kubernetes.io/metadata.name": "kube-system"
                                            }
                                        }
                                    }
                                ],
                                "ports": [
                                    {"protocol": "UDP", "port": 53},
                                    {"protocol": "TCP", "port": 53},
                                ],
                            },
                            {
                                "to": api_peers,
                                "ports": [
                                    {"protocol": "TCP", "port": 6443},
                                    {"protocol": "TCP", "port": 443},
                                ],
                            },
                            {
                                "to": [
                                    {
                                        "ipBlock": {
                                            "cidr": "0.0.0.0/0",
                                            "except": [
                                                "10.0.0.0/8",
                                                "172.16.0.0/12",
                                                "192.168.0.0/16",
                                                "169.254.0.0/16",
                                                "127.0.0.0/8",
                                            ],
                                        }
                                    }
                                ],
                                "ports": [{"protocol": "TCP", "port": 443}],
                            },
                        ],
                    },
                }
            )
            names = json.loads(run(command + ["get", "secrets", "-n", "argocd", "-o", "json"]))
            if not any(obj["metadata"]["name"] == "argocd-redis" for obj in names["items"]):
                apply(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "argocd-redis", "namespace": "argocd"},
                        "type": "Opaque",
                        "stringData": {"auth": secrets.token_urlsafe(32)},
                    }
                )
            run(
                command
                + ["apply", "--server-side", "-f", str(PLATFORM / "pending/argocd-eps-rbac.yaml")]
            )
            run(
                [
                    helm,
                    "--kubeconfig",
                    command[2],
                    "--kube-context",
                    CONTEXT,
                    "upgrade",
                    "--install",
                    "argocd",
                    str(archive),
                    "--namespace",
                    "argocd",
                    "-f",
                    str(PLATFORM / "pending/argo-cd-values.yaml"),
                    "--set-json",
                    "global.imagePullSecrets=[]",
                    "--set",
                    "global.image.imagePullPolicy=Never",
                    "--set",
                    "redis.image.imagePullPolicy=Never",
                    "--wait",
                    "--timeout",
                    "150s",
                ]
            )
            for resource in (
                "deployment/argocd-server",
                "deployment/argocd-repo-server",
                "deployment/argocd-redis",
                "statefulset/argocd-application-controller",
            ):
                run(command + ["rollout", "status", resource, "-n", "argocd", "--timeout=90s"])
            redis_secret = json.loads(
                run(command + ["get", "secret", "argocd-redis", "-n", "argocd", "-o", "json"])
            )
            password = base64.b64decode(redis_secret["data"]["auth"])
            response = run(
                command
                + [
                    "exec",
                    "-i",
                    "-n",
                    "argocd",
                    "deployment/argocd-redis",
                    "--",
                    "redis-cli",
                    "--askpass",
                    "ping",
                ],
                password + b"\n",
            )
            if b"PONG" not in response:
                raise RuntimeError("Redis authentication failed")
            pods = json.loads(run(command + ["get", "pods", "-n", "argocd", "-o", "json"]))["items"]
            for pod in pods:
                if pod["spec"].get("imagePullSecrets") or any(
                    container["imagePullPolicy"] != "Never"
                    for container in pod["spec"]["containers"]
                ):
                    raise ValueError(
                        "Local Argo must use preloaded images without registry credentials"
                    )
            services = json.loads(run(command + ["get", "services", "-n", "argocd", "-o", "json"]))[
                "items"
            ]
            if any(service["spec"]["type"] != "ClusterIP" for service in services):
                raise ValueError("Argo services must remain private")
            if json.loads(run(command + ["get", "ingresses", "-n", "argocd", "-o", "json"]))[
                "items"
            ]:
                raise ValueError("Local Argo must not expose an ingress")
            for account in ("argocd-application-controller", "argocd-server"):
                identity = "system:serviceaccount:argocd:" + account
                for resource in ("secrets", "roles", "rolebindings"):
                    # kubectl returns exit 1 for a denial; use the API's review object.
                    review = {
                        "apiVersion": "authorization.k8s.io/v1",
                        "kind": "SubjectAccessReview",
                        "spec": {
                            "user": identity,
                            "groups": [
                                "system:serviceaccounts",
                                "system:serviceaccounts:argocd",
                                "system:authenticated",
                            ],
                            "resourceAttributes": {
                                "namespace": "eps",
                                "verb": "get",
                                "group": ""
                                if resource == "secrets"
                                else "rbac.authorization.k8s.io",
                                "resource": resource,
                            },
                        },
                    }
                    assert (
                        json.loads(
                            run(
                                command + ["create", "-f", "-", "-o", "json"],
                                json.dumps(review).encode(),
                            )
                        )["status"]["allowed"]
                        is False
                    )
            print(
                "PASS: private local Argo ready; EPS Secret/RBAC reads denied. No application sync performed."
            )


if __name__ == "__main__":
    main()
