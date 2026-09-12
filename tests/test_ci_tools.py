"""The installer must reject unverified downloads and archive links."""

import hashlib
import io
import tarfile

import pytest

from scripts.install_ci_tools import verified_binary


def test_verified_download_and_archive_boundary():
    binary = b"test executable"
    pin = {"sha256": hashlib.sha256(binary).hexdigest()}
    assert verified_binary(binary, pin) == binary
    with pytest.raises(ValueError, match="checksum mismatch"):
        verified_binary(b"tampered", {**pin, "member": "tool"})

    for link in (False, True):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            member = tarfile.TarInfo("linux-amd64/tool")
            if link:
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside"
                archive.addfile(member)
            else:
                member.size = len(binary)
                archive.addfile(member, io.BytesIO(binary))
        payload = data.getvalue()
        pin = {"sha256": hashlib.sha256(payload).hexdigest(), "member": member.name}
        if link:
            with pytest.raises(ValueError, match="regular file"):
                verified_binary(payload, pin)
        else:
            assert verified_binary(payload, pin) == binary
