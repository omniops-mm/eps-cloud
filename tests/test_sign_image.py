"""Wrong-run records must never reach the signer."""

import json

import pytest

from scripts import sign_image


def test_signer_checks_record_and_verifier_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "IMAGE_NAME": "web",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_REPOSITORY": "example/eps",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    record = {
        "image": "ghcr.io/example/eps/web@sha256:" + "b" * 64,
        "tag": "ghcr.io/example/eps/web:" + "a" * 40,
        "commit": "a" * 40,
        "run_id": "1",
        "run_attempt": "1",
    }
    calls = []
    monkeypatch.setattr(sign_image.subprocess, "run", lambda args, **kwargs: calls.append(args))
    path = tmp_path / "image-web.json"
    path.write_text(json.dumps(record))
    sign_image.main()
    assert calls[0] == ["cosign", "sign", "--yes", record["image"]]
    verify = calls[1]
    assert verify[verify.index("--certificate-identity") + 1] == (
        "https://github.com/example/eps/.github/workflows/ci.yml@refs/heads/master"
    )
    assert verify[verify.index("--certificate-github-workflow-sha") + 1] == record["commit"]
    for field in ("image", "tag", "commit", "run_id", "run_attempt"):
        calls.clear()
        path.write_text(json.dumps({**record, field: "wrong"}))
        with pytest.raises(ValueError):
            sign_image.main()
        assert not calls
