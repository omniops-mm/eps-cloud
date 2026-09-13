"""Export cached, digest-pinned platform images without registry credentials."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def image_reference(values):
    repository = values.get("repository", "")
    if not isinstance(repository, str):
        return None
    registry = values.get("registry", "")
    if registry == "dhi.io":
        repository = registry + "/" + repository
    if not repository.startswith(("dhi.io/", "ghcr.io/omniops-mm/eps-cloud/argocd")):
        return None
    digest = values.get("digest") or values.get("sha")
    if not digest:
        tag = str(values.get("tag", ""))
        if "@sha256:" not in tag:
            raise ValueError("Platform image is not digest-pinned")
        digest = tag.split("@", 1)[1]
    digest = str(digest).removeprefix("sha256:")
    if (
        ".." in repository.split("/")
        or not re.fullmatch(r"[a-z0-9./_-]+", repository)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ValueError("Invalid platform image reference")
    return repository + "@sha256:" + digest


def platform_images():
    images = set()

    def walk(value):
        if isinstance(value, dict):
            reference = image_reference(value)
            if reference:
                images.add(reference)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for path in (ROOT / "deploy/platform").rglob("*values.yaml"):
        walk(yaml.safe_load(path.read_text()))
    database = yaml.safe_load((ROOT / "deploy/helm/eps/values-production.yaml").read_text())["db"][
        "image"
    ]
    repository, digest = database.split("@", 1)
    images.add(image_reference({"repository": repository.split(":", 1)[0], "digest": digest}))
    return sorted(images)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        raise ValueError("Image archives must remain outside the repository")
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for image in platform_images():
        info = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
        if image not in info.get("RepoDigests", []):
            raise ValueError("Cached image does not match its reviewed digest")
        filename = image.split("sha256:")[1] + ".tar"
        target = args.output / filename
        if target.exists():
            raise FileExistsError(
                "Use a new export directory; existing archives are not overwritten"
            )
        # Filtering docker save by platform rewrites the original index; filter at import instead.
        subprocess.run(["docker", "image", "save", "--output", str(target), image], check=True)
        with target.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        records.append({"image": image, "file": filename, "sha256": checksum})
        print("Exported " + image, flush=True)
    (args.output / "images.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
