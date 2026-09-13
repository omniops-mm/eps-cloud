"""User-run registry bootstrap for eps-v04-dev only. Never logs credentials."""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

CONTEXT = "k3d-eps-v04-dev"
IMAGES = {
    "eps": (
        "dhi.io/postgres:16.15-alpine3.24@sha256:873f9a267256074af3f047f10d9f0015f73aa959194bcea45075eb98afe9b9cb",
        70,
        ["postgres", "--version"],
    ),
    "external-secrets": (
        "dhi.io/external-secrets:2.10.0@sha256:11cb25e0bbcc472943039ddbb3f1dfc8a7268da402f0ba499da8a43f68cc08b8",
        65532,
        ["external-secrets", "--help"],
    ),
}


def run(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, check=False, timeout=180)
    if result.returncode:
        # Some clients echo supplied objects on failure; never relay their output.
        raise RuntimeError(f"{Path(args[0]).name} operation failed; no credential output displayed")
    return result.stdout


def validate_context(config):
    if config.get("current-context") != CONTEXT or len(config.get("clusters", [])) != 1:
        raise ValueError("Expected only the dedicated local test context")
    cluster = config["clusters"][0]["cluster"]
    server = urlsplit(cluster["server"])
    if (
        server.scheme != "https"
        or server.hostname != "127.0.0.1"
        or server.port != 6550
        or cluster.get("insecure-skip-tls-verify")
        or server.username
        or server.password
    ):
        raise ValueError("Refusing a non-local or insecure Kubernetes endpoint")


def registry_auth(config):
    helper = config.get("credHelpers", {}).get("dhi.io") or config.get("credsStore")
    if helper:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", helper):
            raise ValueError("Invalid Docker credential helper")
        executable = shutil.which("docker-credential-" + helper)
        if not executable:
            raise ValueError("Docker credential helper is unavailable")
        record = json.loads(run([executable, "get"], b"dhi.io\n"))
        username, password = record.get("Username"), record.get("Secret")
        if (
            not username
            or not password
            or username == "<token>"
            or not password.startswith("dckr_pat_")
        ):
            raise ValueError("Use a Docker ID and read-only access token to log into dhi.io")
        return base64.b64encode(f"{username}:{password}".encode()).decode()
    record = config.get("auths", {}).get("dhi.io", {})
    auth = record.get("auth", "")
    try:
        username, password = base64.b64decode(auth, validate=True).decode().split(":", 1)
    except (ValueError, UnicodeError) as error:
        raise ValueError("No usable dhi.io login found") from error
    if not username or not password.startswith("dckr_pat_"):
        raise ValueError(
            "Log into dhi.io with a read-only personal access token, not your account password"
        )
    return auth


@contextmanager
def local_cluster():
    k3d = shutil.which("k3d") or str(
        Path.home() / "Desktop/LinuxDev/EPS-reference/EPS-v0.4-work/tools/bin/k3d.exe"
    )
    kubectl = shutil.which("kubectl")
    if not kubectl or not Path(k3d).is_file():
        raise RuntimeError("Existing k3d and kubectl tools are required")
    with tempfile.TemporaryDirectory(prefix="eps-local-kube-") as temporary:
        kubeconfig = Path(temporary) / "config"
        fd = os.open(kubeconfig, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(run([k3d, "kubeconfig", "get", "eps-v04-dev"]))
        command = [kubectl, "--kubeconfig", str(kubeconfig), "--context", CONTEXT]
        config = json.loads(run(command + ["config", "view", "--minify", "-o", "json"]))
        validate_context(config)
        run(command + ["get", "--raw=/readyz"])

        yield command


def main():
    docker_config = (
        Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))) / "config.json"
    )
    auth = registry_auth(json.loads(docker_config.read_text(encoding="utf-8")))
    # Only dhi.io is copied; other stored registry logins never enter the cluster.
    registry = json.dumps({"auths": {"dhi.io": {"auth": auth}}})
    with local_cluster() as command:

        def apply(obj):
            run(
                command + ["apply", "--server-side", "--field-manager=eps-registry", "-f", "-"],
                json.dumps(obj).encode(),
            )

        for namespace in IMAGES:
            apply(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": namespace,
                        "labels": {
                            "pod-security.kubernetes.io/enforce": "restricted",
                            "pod-security.kubernetes.io/enforce-version": "v1.36",
                        },
                    },
                }
            )
            # Send through stdin, never a command argument or a checked-in file.
            apply(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": "dhi-pull", "namespace": namespace},
                    "type": "kubernetes.io/dockerconfigjson",
                    "data": {".dockerconfigjson": base64.b64encode(registry.encode()).decode()},
                }
            )
        print("PASS: dedicated local context and registry Secrets configured")
        for namespace, (image, uid, entrypoint) in IMAGES.items():
            name = "eps-pull-check-" + uuid.uuid4().hex[:12]
            pod = {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": name, "namespace": namespace},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "imagePullSecrets": [{"name": "dhi-pull"}],
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": uid,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "probe",
                            "image": image,
                            "command": entrypoint,
                            "imagePullPolicy": "Always",
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "10m", "memory": "32Mi"},
                                "limits": {"cpu": "250m", "memory": "128Mi"},
                            },
                        }
                    ],
                },
            }
            try:
                apply(pod)
                for _ in range(90):
                    status = json.loads(
                        run(command + ["get", "pod", name, "-n", namespace, "-o", "json"])
                    )["status"]
                    if status.get("phase") == "Succeeded":
                        print(f"PASS: {namespace} authenticated pull and executable check")
                        break
                    if status.get("phase") == "Failed":
                        raise RuntimeError(
                            f"Image probe failed in {namespace}; inspect pod events locally"
                        )
                    time.sleep(2)
                else:
                    raise RuntimeError(f"Image pull timed out in {namespace}")
            finally:
                run(
                    command
                    + ["delete", "pod", name, "-n", namespace, "--ignore-not-found", "--wait=true"]
                )
    print("Probe pods removed. Local namespaces and dhi-pull Secrets retained for installation.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        # No traceback with command input or decoded credential records.
        print(f"REGISTRY REHEARSAL FAILED: {error}")
        raise SystemExit(1) from None
