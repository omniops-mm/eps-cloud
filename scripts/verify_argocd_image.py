"""Verify the Argo package patch and preserve its complete donor inventory."""

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]


def image_files(image: str) -> dict:
    """Hash an exported filesystem without extracting archive paths."""
    container = subprocess.check_output(["docker", "create", image], text=True).strip()
    try:
        with subprocess.Popen(["docker", "export", container], stdout=subprocess.PIPE) as process:
            if process.stdout is None:
                raise RuntimeError("Docker export has no output")
            result = {}
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for member in archive:
                    path = str(PurePosixPath(member.name))
                    if path.startswith("/") or ".." in PurePosixPath(path).parts:
                        raise ValueError("Unsafe exported path")
                    if path in result:
                        raise ValueError("Duplicate exported path")
                    if path in {"etc/hosts", "etc/hostname", "etc/resolv.conf"}:
                        continue
                    digest = ""
                    if member.isfile():
                        stream = archive.extractfile(member)
                        if stream is None:
                            raise ValueError("Unreadable exported file")
                        hasher = hashlib.sha256()
                        while chunk := stream.read(1024 * 1024):
                            hasher.update(chunk)
                        digest = hasher.hexdigest()
                    result[path] = (
                        member.type.decode(),
                        member.uid,
                        member.gid,
                        member.mode,
                        member.linkname,
                        digest,
                    )
            if process.wait() != 0:
                raise RuntimeError("Docker export failed")
            return result
    finally:
        subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)


def check_files(original: dict, candidate: dict, packages: dict) -> None:
    paths = {pin["path"] for pin in packages.values()}
    changed = {
        path
        for path in original.keys() | candidate.keys()
        if original.get(path) != candidate.get(path)
    }
    unexpected = {
        path for path in changed if path not in paths and not path.startswith("var/lib/dpkg/")
    }
    if unexpected:
        raise ValueError(f"Unexpected filesystem changes: {sorted(unexpected)[:10]}")
    for pin in packages.values():
        path = pin["path"]
        if (
            path not in original
            or path not in candidate
            or original[path][:-1] != candidate[path][:-1]
            or candidate[path][-1] != pin["sha256"]
        ):
            raise ValueError("Patched library does not match its reviewed bytes and metadata")


def normalize_npm(value: object) -> None:
    """Restore scoped names from PURLs without dropping inventory components."""
    if isinstance(value, dict):
        purl = value.get("purl", "")
        if purl.startswith("pkg:npm/"):
            identity = unquote(purl.split("?", 1)[0].split("#", 1)[0][8:])
            if identity.startswith("@"):
                package, separator, version = identity.rpartition("@")
                scope, slash, name = package.partition("/")
                if (
                    not separator
                    or not slash
                    or not name
                    or "/" in name
                    or value.get("version") != version
                    or value.get("name") not in {name, package}
                    or value.get("group") not in {None, "", scope}
                ):
                    raise ValueError("Conflicting scoped npm identity")
                value["group"], value["name"] = scope, name
        for child in value.values():
            normalize_npm(child)
    elif isinstance(value, list):
        for child in value:
            normalize_npm(child)


def derived_inventory(attestation: dict, pins: dict) -> dict:
    subjects = attestation.get("subject", [])
    if (
        attestation.get("predicateType") != "https://cyclonedx.org/bom/v1.6"
        or len(subjects) != 1
        or subjects[0].get("digest") != {"sha256": pins["platform_digest"]}
    ):
        raise ValueError("Donor attestation does not match the reviewed platform digest")
    document = copy.deepcopy(attestation["predicate"])
    normalize_npm(document)
    references = {}
    found = set()
    for component in document["components"]:
        name, purl = component.get("name"), component.get("purl", "")
        if name in pins["packages"] and purl.startswith("pkg:deb/debian/"):
            if name in found:
                raise ValueError("Duplicate patched package")
            version = pins["packages"][name]["version"]
            base, separator, qualifiers = purl.partition("?")
            if "@" not in base:
                raise ValueError("Debian component has no versioned PURL")
            references[purl] = (
                base.rsplit("@", 1)[0]
                + "@"
                + quote(version, safe="")
                + (separator + qualifiers if separator else "")
            )
            component["version"] = version
            found.add(name)
    if found != set(pins["packages"]):
        raise ValueError("The donor inventory is missing a patched package")

    def replace(value):
        if isinstance(value, dict):
            return {key: replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        if isinstance(value, str):
            return references.get(value, value)
        return value

    return replace(document)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m scripts.verify_argocd_image IMAGE")
    image = sys.argv[1]
    pins = json.loads((ROOT / "deploy/platform/argocd-image.json").read_text())
    recipe = (ROOT / "Dockerfile.argocd").read_text()
    if not recipe.startswith(f"FROM {pins['donor']} AS original\n") or any(
        f"{name}={pin['version']}" not in recipe for name, pin in pins["packages"].items()
    ):
        raise ValueError("Dockerfile and reviewed pins disagree")
    subprocess.run(["docker", "pull", "--platform=linux/amd64", pins["donor"]], check=True)
    scout = shutil.which("docker-scout")
    command = [scout] if scout else ["docker", "scout"]
    attestation_path = Path("argocd-donor-attestation.json")
    subprocess.run(
        command
        + [
            "attestation",
            "get",
            "--verify",
            "--skip-tlog",
            "--platform",
            "linux/amd64",
            "--predicate-type",
            "https://cyclonedx.org/bom/v1.6",
            "--output",
            str(attestation_path),
            "registry://" + pins["donor"],
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    attestation = json.loads(attestation_path.read_text())
    inventory = derived_inventory(attestation, pins)
    configs = json.loads(
        subprocess.check_output(["docker", "inspect", pins["donor"], image], text=True)
    )
    original_config, candidate_config = (item["Config"] for item in configs)
    for key in ("User", "Entrypoint", "Cmd", "WorkingDir"):
        if original_config.get(key) != candidate_config.get(key):
            raise ValueError(f"Unexpected image configuration: {key}")
    if set(original_config.get("Env", [])) != set(candidate_config.get("Env", [])):
        raise ValueError("Unexpected image environment")
    check_files(image_files(pins["donor"]), image_files(image), pins["packages"])
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "512m",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=32m",
            "--entrypoint",
            "argocd",
            image,
            "version",
            "--client",
        ],
        check=True,
    )
    Path("sbom-argocd.cdx.json").write_text(json.dumps(inventory), encoding="utf-8")
    Path("argocd-integrity.json").write_text(
        json.dumps(
            {
                "image_id": configs[1]["Id"],
                "donor": pins["donor"],
                "donor_platform_digest": pins["platform_digest"],
                "donor_signature_verified": True,
                "transparency_log_verified": False,
                "inventory_is_derived": True,
                "components": len(inventory["components"]),
                "patched_packages": pins["packages"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("Verified runtime preservation and the complete derived Argo inventory.")


if __name__ == "__main__":
    main()
