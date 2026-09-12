"""Read-only app chart checks. Requires Helm, kubeconform and the dev dependencies."""

import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


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
        if obj["kind"] == "Deployment" and (
            "replicas" in obj["spec"] or obj["spec"]["template"]["spec"].get("initContainers")
        ):
            raise ValueError("GitOps must use the migration Job and leave replicas to HPA")
    jobs = [obj for obj in objects if obj["kind"] == "Job"]
    if (
        len(jobs) != 1
        or jobs[0]["metadata"]["annotations"].get("argocd.argoproj.io/hook") != "Sync"
    ):
        raise ValueError("GitOps requires one blocking Sync migration hook")


def main() -> None:
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
                if production and mode == "job":
                    validate_gitops(objects)
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
    print("App schema/security/raw checks passed; no cluster or platform validation performed")


if __name__ == "__main__":
    main()
