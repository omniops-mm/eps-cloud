"""Record the repository digest returned by Docker after a successful push."""

import json
import os
import re
import subprocess


def published_reference(remote_image: str, repo_digests: list[str]) -> str:
    repository = remote_image.rsplit(":", 1)[0]
    matches = [ref for ref in repo_digests if ref.startswith(repository + "@")]
    if len(matches) != 1 or not re.fullmatch(
        re.escape(repository) + r"@sha256:[0-9a-f]{64}", matches[0]
    ):
        raise ValueError("Expected exactly one SHA256 digest for the published repository")
    return matches[0]


def validate_record(
    record: dict, repository: str, name: str, commit: str, run_id: str, run_attempt: str
) -> str:
    """Bind a digest record to one image and one release run."""
    tag = f"ghcr.io/{repository}/{name}:{commit}"
    if (
        record.get("commit") != commit
        or record.get("tag") != tag
        or record.get("run_id") != run_id
        or record.get("run_attempt") != run_attempt
    ):
        raise ValueError("Image record does not belong to this release run")
    return published_reference(tag, [record["image"]])


def main() -> None:
    remote_image = os.environ["REMOTE_IMAGE"]
    digests = json.loads(
        subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", remote_image],
            text=True,
        )
    )
    reference = published_reference(remote_image, digests or [])
    print(
        json.dumps(
            {
                "image": reference,
                "tag": remote_image,
                "commit": os.environ["GITHUB_SHA"],
                "run_id": os.environ["GITHUB_RUN_ID"],
                "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
