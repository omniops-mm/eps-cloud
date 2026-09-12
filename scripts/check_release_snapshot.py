"""Validate the rendered snapshot before CI publishes its artifact."""

import json
from pathlib import Path

import yaml

from scripts.check_deploy import validate_gitops, validate_objects


def validate_snapshot(objects: list[dict], images: dict[str, str]) -> None:
    validate_objects(objects)
    validate_gitops(objects)
    for obj in objects:
        kind = obj["kind"]
        if kind not in {"Deployment", "Job", "CronJob"}:
            continue
        spec = obj["spec"]
        if kind == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        pod = spec["template"]["spec"]
        expected = images["worker" if kind == "CronJob" else "web"]
        for container in pod.get("initContainers", []) + pod["containers"]:
            if container["image"] != expected:
                raise ValueError("Rendered workload does not use its recorded digest")


def main() -> None:
    root = Path("release-snapshot")
    release = json.loads((root / "release.json").read_text())
    objects = [o for o in yaml.safe_load_all((root / "rendered.yaml").read_text()) if o]
    validate_snapshot(objects, release["images"])
    print("App digest snapshot validated; platform images and live deployment gates remain open")


if __name__ == "__main__":
    main()
