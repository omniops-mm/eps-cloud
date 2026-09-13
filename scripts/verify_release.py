"""Verify a successful CI snapshot before an operator commits it to production."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from scripts.prepare_release import prepare_snapshot

REPOSITORY = "omniops-mm/eps-cloud"


def snapshot_files(root: Path) -> dict[str, bytes]:
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Snapshot symlinks are forbidden")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            # This derived preview is never part of the production chart.
            if name != "rendered.yaml":
                files[name] = path.read_bytes()
    return files


def verify_release(root: Path, snapshot: Path) -> None:
    release = json.loads((snapshot / "release.json").read_text())
    run_id = str(release["run_id"])
    if not run_id.isdecimal():
        raise ValueError("Expected a numeric CI run ID")
    run = json.loads(
        subprocess.check_output(
            ["gh", "api", f"repos/{REPOSITORY}/actions/runs/{run_id}"],
            cwd=root,
        )
    )
    if (
        run.get("conclusion") != "success"
        or run.get("status") != "completed"
        or run.get("event") != "push"
        or run.get("head_branch") != "master"
        or run.get("path") != ".github/workflows/ci.yml"
        or run.get("repository", {}).get("full_name") != REPOSITORY
        or run.get("head_sha") != release["commit"]
        or str(run.get("run_attempt")) != str(release["run_attempt"])
    ):
        raise ValueError("Snapshot is not bound to a successful master CI run")
    with tempfile.TemporaryDirectory(prefix="eps-verify-release-") as temporary:
        records = Path(temporary) / "records"
        records.mkdir()
        for image in ("web", "worker"):
            subprocess.check_output(
                [
                    "gh",
                    "run",
                    "download",
                    run_id,
                    "--repo",
                    REPOSITORY,
                    "--name",
                    f"image-digest-{image}",
                    "--dir",
                    str(records),
                ],
                cwd=root,
            )
        expected = Path(temporary) / "expected"
        prepare_snapshot(
            root,
            records,
            expected,
            REPOSITORY,
            release["commit"],
            run_id,
            str(release["run_attempt"]),
        )
        if snapshot_files(snapshot) != snapshot_files(expected):
            raise ValueError("Snapshot differs from the exact source and image records")
    current = (
        subprocess.check_output(["git", "ls-remote", "origin", "refs/heads/master"], cwd=root)
        .decode()
        .split()
    )
    if current != [release["commit"], "refs/heads/master"]:
        raise ValueError("Source was superseded; verify the newer successful run")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    verify_release(Path.cwd(), args.snapshot)
    print(
        "Snapshot verified. No files were staged and no commit, push or deployment was performed."
    )


if __name__ == "__main__":
    main()
