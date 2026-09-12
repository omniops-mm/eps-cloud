"""Publish a checked snapshot only from the gated CI promotion job.

Standalone execution is not signature verification. CI supplies same-run artifacts
and depends on successful signing and snapshot validation. Never force-push.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from scripts.prepare_release import prepare_snapshot


def snapshot_files(root: Path) -> dict[str, bytes]:
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Snapshot symlinks are forbidden")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            if name != "rendered.yaml":
                files[name] = path.read_bytes()
    return files


def assert_current(commit: str, remote_listing: str) -> None:
    if remote_listing.split() != [commit, "refs/heads/master"]:
        raise ValueError("Source was superseded; promote the newer successful run")


def promote(root: Path, snapshot: Path, records: Path) -> None:
    if (
        os.environ.get("EPS_PROMOTION_ENABLED") != "true"
        or os.environ.get("GITHUB_EVENT_NAME") != "push"
        or os.environ.get("GITHUB_REF") != "refs/heads/master"
        or os.environ.get("GITHUB_REPOSITORY") != "omniops-mm/eps-cloud"
    ):
        raise ValueError("Promotion is disabled outside the approved master CI job")
    commit = os.environ["GITHUB_SHA"]
    with tempfile.TemporaryDirectory(prefix="eps-promotion-") as temporary:
        work = Path(temporary)
        expected = work / "expected"
        prepare_snapshot(
            root,
            records,
            expected,
            os.environ["GITHUB_REPOSITORY"],
            commit,
            os.environ["GITHUB_RUN_ID"],
            os.environ["GITHUB_RUN_ATTEMPT"],
        )
        files = snapshot_files(expected)
        if snapshot_files(snapshot) != files:
            raise ValueError("Snapshot differs from the exact source and image records")

        def git(*args: str, data: bytes | None = None, env: dict | None = None) -> str:
            return (
                subprocess.check_output(["git", *args], cwd=root, input=data, env=env)
                .decode()
                .strip()
            )

        assert_current(commit, git("ls-remote", "origin", "refs/heads/master"))
        parent = git("ls-remote", "origin", "refs/heads/production")
        parents = []
        if parent:
            git("fetch", "--no-tags", "origin", "refs/heads/production")
            parents = ["-p", git("rev-parse", "FETCH_HEAD")]
        env = dict(
            os.environ,
            GIT_INDEX_FILE=str(work / "index"),
            GIT_AUTHOR_NAME="github-actions[bot]",
            GIT_COMMITTER_NAME="github-actions[bot]",
            GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
            GIT_COMMITTER_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
        )
        git("read-tree", "--empty", env=env)
        for name, content in sorted(files.items()):
            blob = git("hash-object", "-w", "--stdin", data=content)
            git("update-index", "--add", "--cacheinfo", "100644", blob, name, env=env)
        tree = git("write-tree", env=env)
        promoted = git("commit-tree", tree, *parents, "-m", f"Promote EPS {commit}", env=env)
        # Recheck after preparing the commit. A simultaneous production update must
        # fail the normal fast-forward push; never retry by overwriting it.
        assert_current(commit, git("ls-remote", "origin", "refs/heads/master"))
        git("push", "origin", f"{promoted}:refs/heads/production")


def main() -> None:
    promote(Path.cwd(), Path("release-snapshot"), Path("release-records"))


if __name__ == "__main__":
    main()
