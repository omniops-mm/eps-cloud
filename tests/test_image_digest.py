"""A release record must name the pushed repository and fail on ambiguity."""

import pytest

from scripts.record_image_digest import published_reference


def test_published_digest_selection():
    tag = "ghcr.io/example/eps/web:commit"
    reference = "ghcr.io/example/eps/web@sha256:" + "a" * 64
    unrelated = "ghcr.io/example/eps/worker@sha256:" + "b" * 64
    assert published_reference(tag, [unrelated, reference]) == reference
    for invalid in ([], [unrelated], [reference, reference], [reference[:-1]]):
        with pytest.raises(ValueError, match="exactly one SHA256"):
            published_reference(tag, invalid)
