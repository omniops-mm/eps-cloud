"""Keep image archives tied to exact reviewed references."""

import pytest

from scripts.export_lab_images import image_reference, platform_images


def test_platform_image_pins():
    digest = "a" * 64
    expected = "dhi.io/redis@sha256:" + digest
    assert image_reference({"registry": "dhi.io", "repository": "redis", "sha": digest}) == expected
    assert image_reference({"repository": "dhi.io/redis", "tag": "8@sha256:" + digest}) == expected
    with pytest.raises(ValueError):
        image_reference({"repository": "dhi.io/redis", "tag": "latest"})
    with pytest.raises(ValueError):
        image_reference({"repository": "dhi.io/../redis", "digest": "sha256:" + digest})
    assert len(platform_images()) == 16
    assert all("@sha256:" in image for image in platform_images())
