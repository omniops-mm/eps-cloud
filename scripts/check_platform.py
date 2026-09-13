"""Validate pinned platform charts and custom resources without a cluster."""

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "deploy/platform"


def validate_eso_role_extensions(obj: dict) -> None:
    labels = obj.get("metadata", {}).get("labels", {})
    if (
        any(
            key.startswith("rbac.authorization.k8s.io/aggregate-to-")
            and str(value).lower() == "true"
            for key, value in labels.items()
        )
        or str(labels.get("servicebinding.io/controller", "false")).lower() == "true"
    ):
        raise ValueError("Unexpected ESO default-role or service-binding permissions")


def validate_eso_scope(objects: list[dict], namespace: str) -> None:
    for obj in objects:
        if obj["kind"] == "Deployment":
            pod = obj["spec"]["template"]["spec"]
            if pod.get("imagePullSecrets") != [{"name": "dhi-pull"}] or any(
                not c["image"].startswith("dhi.io/external-secrets:2.10.0@sha256:")
                for c in pod["containers"]
            ):
                raise ValueError("ESO must use reviewed digest images and private registry access")
        if obj["kind"] in {"ClusterRole", "ClusterRoleBinding"}:
            raise ValueError("ESO must not receive cluster-wide RBAC")
        if obj["kind"] == "Role":
            for rule in obj.get("rules", []):
                if (
                    "secrets" in rule.get("resources", [])
                    and obj["metadata"].get("namespace") != namespace
                ):
                    raise ValueError("ESO Secret permissions escaped its namespace")
                if "serviceaccounts/token" in rule.get("resources", []):
                    raise ValueError("Chart must not grant unrestricted token creation")
    controller = next(
        o
        for o in objects
        if o["kind"] == "Deployment" and o["metadata"]["name"] == f"external-secrets-{namespace}"
    )
    args = controller["spec"]["template"]["spec"]["containers"][0]["args"]
    if f"--namespace={namespace}" not in args:
        raise ValueError("ESO controller must watch only its target namespace")


def validate_federation(identities: list[dict], stores: list[dict]) -> None:
    indexed = {(o["kind"], o["metadata"]["namespace"]): o for o in identities}
    for namespace in ("eps", "monitoring"):
        account = indexed[("ServiceAccount", namespace)]
        if (
            account["metadata"]["name"] != "eps-secrets"
            or account.get("automountServiceAccountToken") is not False
        ):
            raise ValueError("Federation identity must not automount tokens")
        role = indexed[("Role", namespace)]
        expected = [
            {
                "apiGroups": [""],
                "resources": ["serviceaccounts/token"],
                "resourceNames": ["eps-secrets"],
                "verbs": ["create"],
            }
        ]
        binding = indexed[("RoleBinding", namespace)]
        if (
            role["rules"] != expected
            or binding["subjects"]
            != [
                {
                    "kind": "ServiceAccount",
                    "name": f"external-secrets-{namespace}",
                    "namespace": "external-secrets",
                }
            ]
            or binding["roleRef"]
            != {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": role["metadata"]["name"],
            }
        ):
            raise ValueError("Federation token permissions exceed the intended identity")
        store = next(o for o in stores if o["metadata"]["namespace"] == namespace)
        provider = store["spec"]["provider"]["gcpsm"]
        auth = provider.get("auth", {})
        federation = auth.get("workloadIdentityFederation", {})
        if (
            set(auth) != {"workloadIdentityFederation"}
            or set(federation) != {"audience", "serviceAccountRef"}
            or federation["serviceAccountRef"]
            != {
                "name": "eps-secrets",
                "audiences": ["https:" + federation["audience"]],
            }
            or not federation["audience"].startswith("//iam.googleapis.com/projects/")
            or not provider.get("projectID")
        ):
            raise ValueError("SecretStore must explicitly use the scoped federation identity")


def main() -> None:
    pins = json.loads((PLATFORM / "versions.json").read_text(encoding="utf-8"))
    identities = list(yaml.safe_load_all((PLATFORM / "eso-identities.yaml").read_text()))
    stores = list(yaml.safe_load_all((PLATFORM / "secret-store.yaml.example").read_text()))
    validate_federation(identities, stores)
    schemas = {}
    core = []
    custom: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="eps-platform-check-") as temporary:
        work = Path(temporary)
        for name, pin in pins.items():
            subprocess.run(
                [
                    "helm",
                    "pull",
                    name,
                    "--repo",
                    pin["repository"],
                    "--version",
                    pin["version"],
                    "--destination",
                    str(work),
                ],
                check=True,
            )
            archive = work / f"{name}-{pin['version']}.tgz"
            if hashlib.sha256(archive.read_bytes()).hexdigest() != pin["sha256"]:
                raise ValueError(f"Chart checksum mismatch: {name}")
            releases = (
                [(name, None)]
                if name != "external-secrets"
                else [
                    ("external-secrets-eps", "eps"),
                    ("external-secrets-monitoring", "monitoring"),
                ]
            )
            for release, namespace in releases:
                args = [
                    "helm",
                    "template",
                    release,
                    str(archive),
                    "--namespace",
                    name,
                    "--include-crds",
                    "-f",
                    str(PLATFORM / f"{name}-values.yaml"),
                ]
                if namespace:
                    args += ["-f", str(PLATFORM / f"{release}-values.yaml")]
                output = subprocess.check_output(args, text=True)
                objects = [obj for obj in yaml.safe_load_all(output) if obj]
                if namespace:
                    validate_eso_scope(objects, namespace)
                for obj in objects:
                    if name == "external-secrets":
                        validate_eso_role_extensions(obj)
                    if obj["kind"] == "CustomResourceDefinition":
                        spec = obj["spec"]
                        for version in spec["versions"]:
                            schemas[
                                (spec["group"] + "/" + version["name"], spec["names"]["kind"])
                            ] = version["schema"]["openAPIV3Schema"]
                    else:
                        core.append(obj)
        # The standard catalogue omits CRD objects. Check their embedded schemas;
        # CRD admission and Kubernetes-specific schema extensions still need a cluster.
        for schema in schemas.values():
            jsonschema.Draft7Validator.check_schema(schema)
        for filename in ("namespaces.yaml", "metadata-policy.yaml", "eso-identities.yaml"):
            core.extend(o for o in yaml.safe_load_all((PLATFORM / filename).read_text()) if o)
        for filename in (
            "external-secrets.yaml",
            "secret-store.yaml.example",
            "eso-webhook-issuer.yaml",
        ):
            custom.extend(o for o in yaml.safe_load_all((PLATFORM / filename).read_text()) if o)
        custom += [obj for obj in core if (obj["apiVersion"], obj["kind"]) in schemas]
        core = [obj for obj in core if (obj["apiVersion"], obj["kind"]) not in schemas]
        for obj in custom:
            schema = schemas[(obj["apiVersion"], obj["kind"])]
            jsonschema.Draft7Validator(schema).validate(obj)
        target = work / "core.yaml"
        target.write_text(yaml.safe_dump_all(core), encoding="utf-8")
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
    print(f"Validated {len(custom)} custom resources against pinned chart schemas")
    print("Static checks only; image/RBAC/authentication gates and live checks remain open")


if __name__ == "__main__":
    main()
