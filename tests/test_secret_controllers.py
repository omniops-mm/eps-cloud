import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.rehearse_secret_controllers import verify_archive


def test_modified_chart_is_refused():
    with TemporaryDirectory() as directory:
        archive = Path(directory) / "chart"
        archive.write_bytes(b"reviewed")
        digest = hashlib.sha256(b"reviewed").hexdigest()
        verify_archive(archive, digest)
        archive.write_bytes(b"changed")
        try:
            verify_archive(archive, digest)
        except ValueError:
            pass
        else:
            raise AssertionError("Modified chart accepted")
