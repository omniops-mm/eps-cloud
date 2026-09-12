"""Promotion gates reject stale sources and modified snapshot content."""

import pytest

from scripts.promote import assert_current, promote, snapshot_files


def test_promotion_boundaries(tmp_path, monkeypatch):
    commit = "a" * 40
    assert_current(commit, commit + "\trefs/heads/master\n")
    for listing in ("", "b" * 40 + "\trefs/heads/master", commit + "\trefs/heads/production"):
        with pytest.raises(ValueError, match="superseded"):
            assert_current(commit, listing)
    monkeypatch.delenv("EPS_PROMOTION_ENABLED", raising=False)
    with pytest.raises(ValueError, match="disabled"):
        promote(tmp_path, tmp_path, tmp_path)
    (tmp_path / "chart").mkdir()
    (tmp_path / "chart/values.yaml").write_text("image: digest")
    before = snapshot_files(tmp_path)
    (tmp_path / "chart/values.yaml").write_text("image: mutable")
    assert snapshot_files(tmp_path) != before


def test_promotion_never_force_pushes(tmp_path, monkeypatch):
    commit = "a" * 40
    for key, value in {
        "EPS_PROMOTION_ENABLED": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_REPOSITORY": "omniops-mm/eps-cloud",
        "GITHUB_SHA": commit,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "release.json").write_text("{}")

    def prepare(root, records, output, *args):
        output.mkdir()
        (output / "release.json").write_text("{}")

    monkeypatch.setattr("scripts.promote.prepare_snapshot", prepare)
    calls = []
    stale = False

    def git(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["ls-remote", "origin"]:
            if args[3] == "refs/heads/production":
                return b""
            head = (
                "b" * 40 if stale and len([c for c in calls if c[1] == "ls-remote"]) > 2 else commit
            )
            return (head + "\trefs/heads/master").encode()
        return ("c" * 40).encode()

    monkeypatch.setattr("scripts.promote.subprocess.check_output", git)
    promote(tmp_path, snapshot, tmp_path)
    assert calls[-1] == ["git", "push", "origin", "c" * 40 + ":refs/heads/production"]
    calls.clear()
    stale = True
    with pytest.raises(ValueError, match="superseded"):
        promote(tmp_path, snapshot, tmp_path)
    assert not any(call[1] == "push" for call in calls)
    (snapshot / "extra-secret.txt").write_text("unexpected")
    calls.clear()
    with pytest.raises(ValueError, match="differs"):
        promote(tmp_path, snapshot, tmp_path)
    assert calls == []
