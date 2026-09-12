"""Sign and verify only this CI run's recorded image digest."""

import json
import os
import subprocess
from pathlib import Path

from scripts.record_image_digest import published_reference


def main() -> None:
    name = os.environ["IMAGE_NAME"]
    commit = os.environ["GITHUB_SHA"]
    repository = os.environ["GITHUB_REPOSITORY"]
    record = json.loads(Path(f"image-{name}.json").read_text(encoding="utf-8"))
    tag = f"ghcr.io/{repository}/{name}:{commit}"
    if (
        record["commit"] != commit
        or record["tag"] != tag
        or record["run_id"] != os.environ["GITHUB_RUN_ID"]
        or record["run_attempt"] != os.environ["GITHUB_RUN_ATTEMPT"]
    ):
        raise ValueError("Image record does not belong to this release run")
    image = published_reference(tag, [record["image"]])
    identity = f"https://github.com/{repository}/.github/workflows/ci.yml@refs/heads/master"
    subprocess.run(["cosign", "sign", "--yes", image], check=True)
    with Path(f"verified-{name}.json").open("w", encoding="utf-8") as output:
        subprocess.run(
            [
                "cosign",
                "verify",
                image,
                "--certificate-identity",
                identity,
                "--certificate-oidc-issuer",
                "https://token.actions.githubusercontent.com",
                "--certificate-github-workflow-sha",
                commit,
                "--certificate-github-workflow-trigger",
                "push",
            ],
            check=True,
            stdout=output,
        )


if __name__ == "__main__":
    main()
