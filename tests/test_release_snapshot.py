"""A snapshot must use one run's digests and never copy worktree-only files."""

import io
import json
import tarfile
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scripts.prepare_release import prepare_snapshot
from scripts.record_image_digest import validate_record


def test_release_snapshot(tmp_path, monkeypatch):
    commit = "a" * 40
    repository = "omniops-mm/eps-cloud"
    records = tmp_path / "records"
    records.mkdir()
    for name in ("web", "worker"):
        record = {
            "image": f"ghcr.io/{repository}/{name}@sha256:" + "b" * 64,
            "tag": f"ghcr.io/{repository}/{name}:{commit}",
            "commit": commit,
            "run_id": "123",
            "run_attempt": "1",
        }
        (records / f"image-{name}.json").write_text(json.dumps(record))
    root = Path(__file__).resolve().parents[1]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in (root / "deploy/helm/eps").rglob("*"):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(root).as_posix())

    def git_archive(args, cwd):
        assert args == ["git", "archive", "--format=tar", commit, "deploy/helm/eps"]
        return buffer.getvalue()

    monkeypatch.setattr("scripts.prepare_release.subprocess.check_output", git_archive)
    output = tmp_path / "snapshot"
    prepare_snapshot(root, records, output, repository, commit, "123", "1")
    values = yaml.safe_load((output / "chart/values-production.yaml").read_text())
    assert b"\r\n" not in (output / "chart/values-production.yaml").read_bytes()
    assert b"\r\n" not in (output / "release.json").read_bytes()
    assert values["image"]["webDigest"] == "sha256:" + "b" * 64
    assert values["image"]["workerDigest"] == "sha256:" + "b" * 64
    assert json.loads((output / "release.json").read_text())["deployment_ready"] is False
    with pytest.raises(FileExistsError):
        prepare_snapshot(root, records, output, repository, commit, "123", "1")
    for key, value in (
        ("run_id", "999"),
        ("run_attempt", "2"),
        ("commit", "c" * 40),
        ("tag", "unrelated"),
        ("image", "ghcr.io/other/web@sha256:" + "b" * 64),
    ):
        changed = deepcopy(record)
        changed[key] = value
        with pytest.raises(ValueError):
            validate_record(changed, repository, "worker", commit, "123", "1")
    with pytest.raises(ValueError):
        prepare_snapshot(root, records, tmp_path / "rejected", repository, commit, "999", "1")
    assert not (tmp_path / "rejected").exists()

    buffer.seek(0)
    buffer.truncate()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("deploy/helm/eps/../../outside")
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unsafe chart"):
        prepare_snapshot(root, records, tmp_path / "unsafe", repository, commit, "123", "1")
    assert not (tmp_path / "unsafe").exists()
    assert not (tmp_path / "outside").exists()
