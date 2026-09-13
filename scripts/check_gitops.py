"""Exercise migration-failure gating in the existing private local Argo app."""

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import yaml

from scripts.rehearse_registry import local_cluster, run
from scripts.verify_release import snapshot_files, verify_release


def wait_operation(command, expected):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        app = json.loads(run(command + ["get", "application", "eps", "-n", "argocd", "-o", "json"]))
        state = app.get("status", {}).get("operationState", {})
        if not app.get("operation") and state.get("phase") in {"Succeeded", "Failed", "Error"}:
            if state["phase"] != expected:
                raise RuntimeError(
                    "Unexpected sync result: " + state["phase"] + ": " + state.get("message", "")
                )
            return app
        time.sleep(2)
    raise TimeoutError("Argo synchronization exceeded the rehearsal deadline")


def request_sync(command, revision, manifests=None, source=None):
    app = json.loads(run(command + ["get", "application", "eps", "-n", "argocd", "-o", "json"]))
    if app.get("operation"):
        raise RuntimeError("An Argo operation is already active")
    sync = {"revision": revision, "prune": False}
    if source is not None:
        sync["source"] = source
    if manifests is not None:
        sync["manifests"] = [json.dumps(obj) for obj in manifests]
    app["operation"] = {"initiatedBy": {"username": "local-operator-rehearsal"}, "sync": sync}
    app.pop("status", None)
    app["metadata"].pop("managedFields", None)
    run(command + ["replace", "-f", "-"], json.dumps(app).encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    verify_release(Path.cwd(), args.snapshot)
    revision = subprocess.check_output(
        ["git", "ls-remote", "origin", "refs/heads/production"], text=True
    ).split()[0]
    tree = json.loads(
        subprocess.check_output(
            ["gh", "api", f"repos/omniops-mm/eps-cloud/git/trees/{revision}?recursive=1"]
        )
    )
    files = snapshot_files(args.snapshot)
    actual = {entry["path"]: entry["sha"] for entry in tree["tree"] if entry["type"] == "blob"}
    if tree.get("truncated") or set(files) != set(actual):
        raise ValueError("Production does not contain exactly the verified snapshot")
    for name, data in files.items():
        if (
            hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            != actual[name]
        ):
            raise ValueError("Production snapshot file mismatch")
    helm = shutil.which("helm")
    if not helm:
        raise RuntimeError("Helm is required")
    with local_cluster() as command:
        app = json.loads(run(command + ["get", "application", "eps", "-n", "argocd", "-o", "json"]))
        if app["spec"].get("syncPolicy", {}).get("automated") is not None:
            raise ValueError("Automatic synchronization must remain disabled")
        if (
            app["status"]["sync"]["status"] != "Synced"
            or app["status"]["health"]["status"] != "Healthy"
        ):
            raise ValueError("Start from a healthy synchronized application")
        original_claims = {
            p["metadata"]["name"]: p["metadata"]["uid"]
            for p in json.loads(run(command + ["get", "pvc", "-n", "eps", "-o", "json"]))["items"]
        }
        values = app["spec"]["source"]["helm"]["valuesObject"]
        raw = run(
            [
                helm,
                "template",
                "eps",
                str(args.snapshot / "chart"),
                "--namespace",
                "eps",
                "-f",
                str(args.snapshot / "chart/values-production.yaml"),
                "-f",
                str(args.snapshot / "chart/values-gitops.yaml"),
                "-f",
                "-",
            ],
            yaml.safe_dump(values).encode(),
        )
        objects = [obj for obj in yaml.safe_load_all(raw) if obj]
        migration = next(
            obj
            for obj in objects
            if obj["kind"] == "Job" and obj["metadata"]["name"] == "eps-migrate"
        )
        migration["spec"]["template"]["spec"]["containers"][0]["command"] = [
            "python",
            "-c",
            "raise SystemExit(42)",
        ]
        web = next(
            obj
            for obj in objects
            if obj["kind"] == "Deployment" and obj["metadata"]["name"] == "web"
        )
        marker = uuid.uuid4().hex
        web["spec"]["template"]["metadata"].setdefault("annotations", {})[
            "eps.test/migration-gate"
        ] = marker
        original_web = json.loads(
            run(command + ["get", "deployment", "web", "-n", "eps", "-o", "json"])
        )["spec"]["template"]
        try:
            request_sync(command, revision, objects)
            failed = wait_operation(command, "Failed")
            hooks = failed["status"]["operationState"]["syncResult"]["resources"]
            if not any(
                obj["name"] == "eps-migrate" and obj.get("hookPhase") == "Failed" for obj in hooks
            ):
                raise ValueError("Failure did not originate in the migration hook")
            current_web = json.loads(
                run(command + ["get", "deployment", "web", "-n", "eps", "-o", "json"])
            )["spec"]["template"]
            if current_web != original_web:
                raise ValueError("The web template changed after a failed migration")
            print("PASS: failed migration blocks the later web rollout", flush=True)
        finally:
            request_sync(command, revision)
            wait_operation(command, "Succeeded")
        # Exercise the native rollback source field without publishing a test release.
        baseline = json.loads(
            run(command + ["get", "application", "eps", "-n", "argocd", "-o", "json"])
        )
        previous = baseline["status"]["history"][-1]
        changed = copy.deepcopy(previous["source"])
        changed["helm"]["valuesObject"]["tracing"]["sampleRate"] = 0.5
        baseline_template = json.loads(
            run(command + ["get", "deployment", "web", "-n", "eps", "-o", "json"])
        )["spec"]["template"]
        try:
            request_sync(command, revision, source=changed)
            wait_operation(command, "Succeeded")
            run(command + ["rollout", "status", "deployment/web", "-n", "eps", "--timeout=90s"])
            changed_template = json.loads(
                run(command + ["get", "deployment", "web", "-n", "eps", "-o", "json"])
            )["spec"]["template"]
            if changed_template == baseline_template:
                raise ValueError("Configuration rollout did not change the pod template")
        finally:
            request_sync(command, previous["revision"], source=previous["source"])
            wait_operation(command, "Succeeded")
            run(command + ["rollout", "status", "deployment/web", "-n", "eps", "--timeout=90s"])
        restored_template = json.loads(
            run(command + ["get", "deployment", "web", "-n", "eps", "-o", "json"])
        )["spec"]["template"]
        if restored_template != baseline_template:
            raise ValueError("Native rollback did not restore the previous pod template")
        print(
            "PASS: native Argo configuration rollback restored the healthy pod template", flush=True
        )
        claims = {
            p["metadata"]["name"]: p["metadata"]["uid"]
            for p in json.loads(run(command + ["get", "pvc", "-n", "eps", "-o", "json"]))["items"]
        }
        if claims != original_claims:
            raise ValueError("Database claim identity changed")
        jobs = json.loads(run(command + ["get", "cronjobs", "-n", "eps", "-o", "json"]))["items"]
        if not all(job["spec"].get("suspend") for job in jobs):
            raise ValueError("A scheduled job was unsuspended")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            app = json.loads(
                run(command + ["get", "application", "eps", "-n", "argocd", "-o", "json"])
            )
            if (
                app["status"]["sync"]["status"] == "Synced"
                and app["status"]["health"]["status"] == "Healthy"
            ):
                break
            time.sleep(2)
        else:
            raise TimeoutError("The restored application did not become Synced and Healthy")
        print("PASS: verified Git version restored; PVCs retained and CronJobs suspended")


if __name__ == "__main__":
    main()
