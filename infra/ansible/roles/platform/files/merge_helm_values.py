"""Merge reviewed Helm values and remove registry pull-secret references."""

import argparse
import sys
from pathlib import Path

import yaml


def merge(left: dict, right: dict) -> dict:
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(left.get(key), dict):
            merge(left[key], value)
        else:
            left[key] = value
    return left


def strip_pull_secrets(value, local_images=False, path=()):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"imagePullSecrets", "pullSecrets"}:
                continue
            item_path = path + (key,)
            item = strip_pull_secrets(item, local_images, item_path)
            if local_images and key == "image" and isinstance(item, dict):
                if item_path in {("global", "image"), ("redis", "image")}:
                    item["imagePullPolicy"] = "Never"
                else:
                    item["pullPolicy"] = "Never"
            result[key] = item
        return result
    if isinstance(value, list):
        return [strip_pull_secrets(item, local_images, path) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-images", action="store_true")
    parser.add_argument("values", type=Path, nargs="+")
    args = parser.parse_args()

    values = {}
    for path in args.values:
        with path.open(encoding="utf-8") as source:
            values = merge(values, yaml.safe_load(source) or {})
    yaml.safe_dump(strip_pull_secrets(values, args.local_images), sys.stdout, sort_keys=False)


if __name__ == "__main__":
    main()
