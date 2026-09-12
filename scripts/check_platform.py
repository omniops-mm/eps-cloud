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


def main() -> None:
    pins = json.loads((PLATFORM / "versions.json").read_text(encoding="utf-8"))
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
            output = subprocess.check_output(
                [
                    "helm",
                    "template",
                    name,
                    str(archive),
                    "--namespace",
                    name,
                    "--include-crds",
                    "-f",
                    str(PLATFORM / f"{name}-values.yaml"),
                ],
                text=True,
            )
            for obj in yaml.safe_load_all(output):
                if not obj:
                    continue
                if name == "external-secrets":
                    validate_eso_role_extensions(obj)
                if obj["kind"] == "CustomResourceDefinition":
                    spec = obj["spec"]
                    for version in spec["versions"]:
                        schemas[(spec["group"] + "/" + version["name"], spec["names"]["kind"])] = (
                            version["schema"]["openAPIV3Schema"]
                        )
                else:
                    core.append(obj)
        # The standard catalogue omits CRD objects. Check their embedded schemas;
        # CRD admission and Kubernetes-specific schema extensions still need a cluster.
        for schema in schemas.values():
            jsonschema.Draft7Validator.check_schema(schema)
        for filename in ("namespaces.yaml", "metadata-policy.yaml", "eso-metadata.yaml"):
            core.extend(o for o in yaml.safe_load_all((PLATFORM / filename).read_text()) if o)
        for filename in ("external-secrets.yaml", "secret-store.yaml.example"):
            custom.extend(o for o in yaml.safe_load_all((PLATFORM / filename).read_text()) if o)
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
