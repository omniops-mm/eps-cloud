"""Reject cloud contexts and copying unrelated registry credentials."""

import base64
from copy import deepcopy

import pytest

from scripts.rehearse_registry import registry_auth, validate_context


def test_registry_boundaries():
    local: dict = {
        "current-context": "k3d-eps-v04-dev",
        "clusters": [{"cluster": {"server": "https://127.0.0.1:6550"}}],
    }
    validate_context(local)
    for url in ("https://example.com:6550", "http://127.0.0.1:6550", "https://127.0.0.1:6443"):
        bad = deepcopy(local)
        bad["clusters"][0]["cluster"]["server"] = url
        with pytest.raises(ValueError):
            validate_context(bad)
    bad = deepcopy(local)
    bad["clusters"][0]["cluster"]["insecure-skip-tls-verify"] = True
    with pytest.raises(ValueError):
        validate_context(bad)
    auth = base64.b64encode(b"test-user:dckr_pat_test-only-placeholder").decode()
    assert (
        registry_auth({"auths": {"dhi.io": {"auth": auth}, "other": {"auth": "private"}}}) == auth
    )
    with pytest.raises(ValueError):
        registry_auth({"auths": {"other": {"auth": auth}}})
    with pytest.raises(ValueError):
        registry_auth({"credsStore": "../../untrusted"})

    password = base64.b64encode(b"test-user:account-password").decode()
    with pytest.raises(ValueError, match="personal access token"):
        registry_auth({"auths": {"dhi.io": {"auth": password}}})
