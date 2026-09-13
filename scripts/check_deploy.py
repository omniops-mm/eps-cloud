"""Read-only app chart checks. Requires Helm, kubeconform and the dev dependencies."""

import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def validate_host_configs(cluster: dict, server: dict, ingress: dict) -> None:
    """Keep local listeners private and the VM's bundled ingress internal."""
    if (
        cluster.get("kubeAPI", {}).get("hostIP") != "127.0.0.1"
        or any(
            not port.get("port", "").startswith("127.0.0.1:") for port in cluster.get("ports", [])
        )
        or server.get("secrets-encryption") is not True
        or server.get("write-kubeconfig-mode") != "0600"
        or "servicelb" not in server.get("disable", [])
    ):
        raise ValueError("Unsafe cluster listener, encryption or kubeconfig settings")
    values = yaml.safe_load(ingress["spec"]["valuesContent"])
    service = values.get("service", {})
    if (
        ingress.get("metadata", {}).get("namespace") != "kube-system"
        or ingress.get("metadata", {}).get("name") != "traefik"
        or service.get("type") != "ClusterIP"
        or service.get("externalIPs")
        or values.get("hostNetwork")
        or any(port.get("hostPort") for port in values.get("ports", {}).values())
        or values.get("logs", {}).get("access", {}).get("enabled") is not False
    ):
        raise ValueError("Traefik must keep ingress internal and access logging disabled")


def validate_objects(objects: list[dict]) -> None:
    """Reject regressions in the app's existing pod security boundary."""
    for obj in objects:
        kind = obj["kind"]
        name = obj["metadata"]["name"]
        if kind in {"Secret", "Namespace", "ClusterRole", "ClusterRoleBinding"}:
            raise ValueError(f"App chart must not own {kind}/{name}")
        if kind not in {"Deployment", "StatefulSet", "Job", "CronJob"}:
            continue
        spec = obj["spec"]
        if kind == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        pod = spec["template"]["spec"]
        security = pod.get("securityContext", {})
        if (
            pod.get("automountServiceAccountToken") is not False
            or security.get("runAsNonRoot") is not True
            or security.get("seccompProfile", {}).get("type") != "RuntimeDefault"
            or any(pod.get(key) for key in ("hostNetwork", "hostPID", "hostIPC"))
            or any("hostPath" in volume for volume in pod.get("volumes", []))
        ):
            raise ValueError(f"Unsafe pod settings: {kind}/{name}")
        for container in pod.get("initContainers", []) + pod["containers"]:
            security = container.get("securityContext", {})
            if (
                security.get("allowPrivilegeEscalation") is not False
                or security.get("privileged", False)
                or "ALL" not in security.get("capabilities", {}).get("drop", [])
                or security.get("capabilities", {}).get("add")
                or (security.get("runAsUser") == 0)
                or (security.get("runAsNonRoot") is False)
                or (kind != "StatefulSet" and security.get("readOnlyRootFilesystem") is not True)
            ):
                raise ValueError(f"Unsafe container settings: {kind}/{name}")


def validate_gitops(objects: list[dict]) -> None:
    """Check the release boundary and migration ordering used by Argo."""
    platform = ROOT / "deploy/platform"
    project = yaml.safe_load((platform / "argocd-project.yaml").read_text())["spec"]
    application = yaml.safe_load((platform / "argocd-application.yaml").read_text())
    spec = application["spec"]
    source = spec["source"]
    repo = "https://github.com/omniops-mm/eps-cloud.git"
    destination = {"server": "https://kubernetes.default.svc", "namespace": "eps"}
    allowed = {
        ("", "Service"),
        ("", "ConfigMap"),
        ("apps", "Deployment"),
        ("apps", "StatefulSet"),
        ("batch", "Job"),
        ("batch", "CronJob"),
        ("autoscaling", "HorizontalPodAutoscaler"),
        ("networking.k8s.io", "Ingress"),
        ("networking.k8s.io", "NetworkPolicy"),
        ("monitoring.coreos.com", "ServiceMonitor"),
        ("monitoring.coreos.com", "PrometheusRule"),
    }
    if (
        project["sourceRepos"] != [repo]
        or project["destinations"] != [destination]
        or project.get("clusterResourceWhitelist") != []
        or {(item["group"], item["kind"]) for item in project["namespaceResourceWhitelist"]}
        != allowed
        or spec["project"] != "eps"
        or spec["destination"] != destination
        or source
        != {
            "repoURL": repo,
            "targetRevision": "production",
            "path": "chart",
            "helm": {
                "releaseName": "eps",
                "valueFiles": ["values-production.yaml", "values-gitops.yaml"],
            },
        }
        or spec.get("ignoreDifferences")
        != [
            {
                "group": "apps",
                "kind": "Deployment",
                "name": "web",
                "namespace": "eps",
                "jsonPointers": ["/spec/replicas"],
            }
        ]
        or "automated" in spec["syncPolicy"]
        or application["metadata"].get("finalizers")
        or set(spec["syncPolicy"]["syncOptions"])
        != {
            "RespectIgnoreDifferences=true",
            "FailOnSharedResource=true",
        }
    ):
        raise ValueError("Unsafe GitOps source, destination or permissions")
    for obj in objects:
        group = obj["apiVersion"].split("/")[0] if "/" in obj["apiVersion"] else ""
        if (group, obj["kind"]) not in allowed:
            raise ValueError("Chart resource exceeds the Argo project allowlist")
        expected = {"StatefulSet": "0", "Job": "1", "Deployment": "2", "CronJob": "2"}.get(
            obj["kind"]
        )
        if (
            expected
            and obj["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-wave")
            != expected
        ):
            raise ValueError("GitOps migration ordering changed")
        if (
            obj["kind"] == "Deployment"
            and obj["metadata"]["name"] == "web"
            and ("replicas" in obj["spec"] or obj["spec"]["template"]["spec"].get("initContainers"))
        ):
            raise ValueError("GitOps must use the migration Job and leave replicas to HPA")
    jobs = [obj for obj in objects if obj["kind"] == "Job"]
    if (
        len(jobs) != 1
        or jobs[0]["metadata"]["annotations"].get("argocd.argoproj.io/hook") != "Sync"
    ):
        raise ValueError("GitOps requires one blocking Sync migration hook")


def validate_tracing(objects: list[dict], enabled: bool) -> None:
    web = next(o for o in objects if o["kind"] == "Deployment" and o["metadata"]["name"] == "web")
    env = web["spec"]["template"]["spec"]["containers"][0]["env"]
    configured = {e["name"]: e.get("value") for e in env if e["name"].startswith("TRACING_")}
    policies = [
        o
        for o in objects
        if o["kind"] == "NetworkPolicy" and o["metadata"]["name"] == "web-to-tempo"
    ]
    if not enabled:
        if configured or policies:
            raise ValueError("Tracing must remain disabled by default")
        return
    if (
        configured != {"TRACING_ENABLED": "true", "TRACING_SAMPLE_RATE": "0.1"}
        or len(policies) != 1
    ):
        raise ValueError("Unexpected tracing enablement or sampling configuration")
    if policies[0]["spec"] != {
        "podSelector": {"matchLabels": {"app": "web"}},
        "policyTypes": ["Egress"],
        "egress": [
            {
                "to": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "monitoring"}
                        },
                        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "tempo"}},
                    }
                ],
                "ports": [{"protocol": "TCP", "port": 4318}],
            }
        ],
    }:
        raise ValueError("Tracing egress must target only the private Tempo HTTP receiver")


def main() -> None:
    validate_host_configs(
        yaml.safe_load((ROOT / "deploy/k3d.yaml").read_text()),
        yaml.safe_load((ROOT / "deploy/platform/k3s-config.yaml").read_text()),
        yaml.safe_load((ROOT / "deploy/platform/traefik.yaml").read_text()),
    )
    chart = str(ROOT / "deploy/helm/eps")
    with tempfile.TemporaryDirectory(prefix="eps-deploy-check-") as temporary:
        work = Path(temporary)
        for production in (False, True):
            for mode in ("init", "job"):
                args = ["helm", "template", "eps", chart, "--namespace", "eps"]
                if production:
                    args += ["-f", str(ROOT / "deploy/helm/eps/values-production.yaml")]
                if production and mode == "job":
                    args += ["-f", str(ROOT / "deploy/helm/eps/values-gitops.yaml")]
                else:
                    args += ["--set", f"migration.mode={mode}"]
                output = subprocess.check_output(args, text=True)
                objects = [obj for obj in yaml.safe_load_all(output) if obj]
                validate_objects(objects)
                validate_tracing(objects, False)
                if production and mode == "job":
                    validate_gitops(objects)
                if production:
                    database = next(o for o in objects if o["kind"] == "StatefulSet")
                    pod = database["spec"]["template"]["spec"]
                    runtime = pod["containers"][0]
                    bootstrap = pod.get("initContainers", [])
                    if (
                        not runtime["image"].startswith("dhi.io/postgres:")
                        or "@sha256:" not in runtime["image"]
                        or pod.get("imagePullSecrets") != [{"name": "dhi-pull"}]
                        or len(bootstrap) != 1
                        or bootstrap[0]["image"] != runtime["image"]
                        or bootstrap[0]["command"] != ["/bin/sh", "/eps-init/bootstrap-db.sh"]
                        or any(
                            e["name"] in {"EPS_DB_PASSWORD", "EPS_EXPORTER_PASSWORD"}
                            for e in runtime["env"]
                        )
                    ):
                        raise ValueError("Production database image/bootstrap boundary changed")
                web = next(o for o in objects if o["kind"] == "Deployment")
                inits = web["spec"]["template"]["spec"].get("initContainers", [])
                jobs = [o for o in objects if o["kind"] == "Job"]
                if (len(inits), len(jobs)) != ((1, 0) if mode == "init" else (0, 1)):
                    raise ValueError("Expected exactly one selected migration mechanism")
                if not production and mode == "init":
                    raw: list[dict] = []
                    for path in sorted((ROOT / "deploy/raw-manifests").glob("*.yaml")):
                        raw.extend(o for o in yaml.safe_load_all(path.read_text()) if o)
                    app_raw = [o for o in raw if o["kind"] != "Namespace"]
                    key = lambda o: (o["kind"], o["metadata"]["name"])
                    if sorted(objects, key=key) != sorted(app_raw, key=key):
                        raise ValueError("Raw examples drifted; run deploy/render_raw.py")
                target = work / "rendered.yaml"
                target.write_text(output, encoding="utf-8")
                subprocess.run(
                    [
                        "kubeconform",
                        "-strict",
                        "-summary",
                        "-kubernetes-version",
                        "1.36.3",
                        str(target),
                    ],
                    check=True,
                )
                print(f"Passed: production={production}, migration={mode}")
        output = subprocess.check_output(
            [
                "helm",
                "template",
                "eps",
                chart,
                "--namespace",
                "eps",
                "--set",
                "tracing.enabled=true",
            ],
            text=True,
        )
        objects = [o for o in yaml.safe_load_all(output) if o]
        validate_objects(objects)
        validate_tracing(objects, True)
        target.write_text(output, encoding="utf-8")
        subprocess.run(
            ["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.36.3", str(target)],
            check=True,
        )
        print("Passed: opt-in tracing and restricted Tempo egress")
    print("App schema/security/raw checks passed; no cluster or platform validation performed")


if __name__ == "__main__":
    main()
