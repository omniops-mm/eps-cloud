"""Build a local chart snapshot; this command never promotes or deploys it.

CI must gate this on successful signing/verification. JSON records alone are not
cryptographic proof. Read the chart from the exact source commit, not the worktree.
"""

import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import yaml

from scripts.record_image_digest import validate_record


def prepare_snapshot(
    root: Path,
    records: Path,
    output: Path,
    repository: str,
    commit: str,
    run_id: str,
    run_attempt: str,
) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a full source commit")
    if output.exists():
        raise FileExistsError("Snapshot destination already exists")
    images = {}
    for name in ("web", "worker"):
        record = json.loads((records / f"image-{name}.json").read_text())
        images[name] = validate_record(record, repository, name, commit, run_id, run_attempt)
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", commit, "deploy/helm/eps"],
        cwd=root,
    )
    # Build privately, then publish the complete directory. Never overwrite a snapshot.
    with tempfile.TemporaryDirectory(prefix="eps-release-", dir=output.parent) as temporary:
        staging = Path(temporary) / "snapshot"
        chart = staging / "chart"
        chart.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            for member in bundle:
                if member.isdir():
                    continue
                path = PurePosixPath(member.name)
                prefix = PurePosixPath("deploy/helm/eps")
                if (
                    not member.isfile()
                    or not path.is_relative_to(prefix)
                    or ".." in path.parts
                    or "\\" in member.name
                    or ":" in member.name
                ):
                    raise ValueError("Unsafe chart archive member")
                content = bundle.extractfile(member)
                if content is None:
                    raise ValueError("Missing chart archive content")
                target = chart.joinpath(*path.relative_to(prefix).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content.read())
        values_path = chart / "values-production.yaml"
        values = yaml.safe_load(values_path.read_text())
        values["image"] = {
            "registry": f"ghcr.io/{repository}",
            "tag": commit,
            "webDigest": images["web"].split("@", 1)[1],
            "workerDigest": images["worker"].split("@", 1)[1],
        }
        values_path.write_text(
            yaml.safe_dump(values, sort_keys=False), encoding="utf-8", newline="\n"
        )
        (staging / "release.json").write_text(
            json.dumps(
                {
                    "commit": commit,
                    "run_id": run_id,
                    "run_attempt": run_attempt,
                    "images": images,
                    "deployment_ready": False,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        staging.rename(output)


def main() -> None:
    prepare_snapshot(
        Path.cwd(),
        Path("release-records"),
        Path("release-snapshot"),
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_SHA"],
        os.environ["GITHUB_RUN_ID"],
        os.environ["GITHUB_RUN_ATTEMPT"],
    )


if __name__ == "__main__":
    main()
