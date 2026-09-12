"""Install verified Linux amd64 release binaries for GitHub Actions."""

import hashlib
import io
import json
import os
import platform
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def verified_binary(data: bytes, pin: dict[str, str]) -> bytes:
    """Check the complete download before reading any archive member."""
    if hashlib.sha256(data).hexdigest() != pin["sha256"]:
        raise ValueError("Tool download checksum mismatch")
    if "member" not in pin:
        return data
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = archive.getmember(pin["member"])
        if not member.isfile():
            raise ValueError("Tool archive member must be a regular file")
        # Read one file into memory; never extract archive paths onto the runner.
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError("Tool archive member is unreadable")
        return stream.read()


def main() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("This installer requires a Linux amd64 GitHub Actions runner")
    github_path = Path(os.environ["GITHUB_PATH"])
    pins = json.loads(Path(__file__).with_name("tools.lock.json").read_text(encoding="utf-8"))
    destination = Path(tempfile.mkdtemp(prefix="eps-tools-", dir=os.environ["RUNNER_TEMP"]))
    for name, pin in pins.items():
        if not pin["url"].startswith("https://"):
            raise ValueError("Tool downloads require HTTPS")
        with urllib.request.urlopen(pin["url"], timeout=60) as response:
            data = verified_binary(response.read(), pin)
        target = destination / name
        target.write_bytes(data)
        target.chmod(0o755)
    # Expose the directory only after every download passes verification.
    with github_path.open("a", encoding="utf-8") as output:
        output.write(str(destination) + "\n")


if __name__ == "__main__":
    main()
