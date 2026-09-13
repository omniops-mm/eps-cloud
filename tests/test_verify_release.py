"""Release verification rejects untrusted runs and never writes Git state."""

import json

import pytest

from scripts.verify_release import verify_release


def test_operator_release_verification(tmp_path, monkeypatch):
    commit = "a" * 40
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    release = {"commit": commit, "run_id": "123", "run_attempt": "1"}
    payload = json.dumps(release)
    (snapshot / "release.json").write_text(payload)
    run = {
        "status": "completed",
        "conclusion": "success",
        "event": "push",
        "head_branch": "master",
        "path": ".github/workflows/ci.yml",
        "repository": {"full_name": "omniops-mm/eps-cloud"},
        "head_sha": commit,
        "run_attempt": 1,
    }
    current = commit

    def prepare(root, records, output, *args):
        output.mkdir()
        (output / "release.json").write_text(payload)

    def read_only(args, **kwargs):
        if args == ["gh", "api", "repos/omniops-mm/eps-cloud/actions/runs/123"]:
            return json.dumps(run).encode()
        if args[:4] == ["gh", "run", "download", "123"]:
            assert args[4:7] == ["--repo", "omniops-mm/eps-cloud", "--name"]
            assert args[7] in ["image-digest-web", "image-digest-worker"]
            assert args[8] == "--dir"
            return b""
        assert args == ["git", "ls-remote", "origin", "refs/heads/master"]
        return (current + "\trefs/heads/master\n").encode()

    monkeypatch.setattr("scripts.verify_release.prepare_snapshot", prepare)
    monkeypatch.setattr("scripts.verify_release.subprocess.check_output", read_only)
    verify_release(tmp_path, snapshot)
    for key, bad in (
        ("conclusion", "failure"),
        ("event", "pull_request"),
        ("head_branch", "production"),
        ("path", ".github/workflows/untrusted.yml"),
        ("head_sha", "b" * 40),
        ("run_attempt", 2),
    ):
        original = run[key]
        run[key] = bad
        with pytest.raises(ValueError, match="successful master"):
            verify_release(tmp_path, snapshot)
        run[key] = original
    current = "b" * 40
    with pytest.raises(ValueError, match="superseded"):
        verify_release(tmp_path, snapshot)
    current = commit
    (snapshot / "extra.txt").write_text("unexpected")
    with pytest.raises(ValueError, match="differs"):
        verify_release(tmp_path, snapshot)


def test_ci_has_no_repository_write_job():
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    assert workflow["permissions"]["contents"] == "read"
    for job in workflow["jobs"].values():
        assert job.get("permissions", {}).get("contents", "read") == "read"
