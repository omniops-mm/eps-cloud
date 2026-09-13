"""Validate opt-in monitoring resources, security boundaries and dashboard queries."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import jsonschema
import yaml

from scripts.check_deploy import ROOT, validate_gitops, validate_objects


def reject_unknown_fields(schema: object) -> None:
    """Close defined objects and normalize equivalent Go/Python regex syntax."""
    if isinstance(schema, dict):
        # Python requires global regex flags before anchors; Go accepts either order.
        if str(schema.get("pattern", "")).startswith("^(?i)"):
            schema["pattern"] = "(?i)^" + schema["pattern"][5:]
        if "properties" in schema and not schema.get("x-kubernetes-preserve-unknown-fields"):
            schema.setdefault("additionalProperties", False)
        for value in schema.values():
            reject_unknown_fields(value)
    elif isinstance(schema, list):
        for value in schema:
            reject_unknown_fields(value)


def validate_monitoring(objects: list[dict], namespace: str) -> None:
    indexed = {(obj["kind"], obj["metadata"]["name"]): obj for obj in objects}
    monitor = indexed[("ServiceMonitor", "eps-web")]
    if monitor["spec"] != {
        "scrapeProtocols": ["OpenMetricsText1.0.0", "PrometheusText0.0.4"],
        "selector": {"matchLabels": {"app": "web"}},
        "namespaceSelector": {"matchNames": [namespace]},
        "endpoints": [
            {
                "port": "http",
                "honorLabels": False,
                "followRedirects": False,
                "path": "/metrics",
                "interval": "15s",
            }
        ],
    }:
        raise ValueError("Web metrics must use the local Service without credentials or redirects")
    service = indexed[("Service", "alert-log")]["spec"]
    if service != {
        "type": "ClusterIP",
        "selector": {"app": "alert-log"},
        "ports": [{"name": "http", "port": 9091, "targetPort": 9091}],
    }:
        raise ValueError("Alert receiver must use an internal Service")
    receiver = indexed[("Deployment", "alert-log")]["spec"]["template"]["spec"]
    web = indexed[("Deployment", "web")]["spec"]["template"]["spec"]
    container = receiver["containers"][0]
    if (
        len(receiver["containers"]) != 1
        or receiver.get("initContainers")
        or receiver.get("volumes")
        or container.get("envFrom")
        or container.get("env")
        or container["image"] != web["containers"][0]["image"]
        or container["command"] != ["python", "alert-log.py"]
    ):
        raise ValueError("Alert receiver must reuse the web image without credentials or volumes")
    for name, target, source, port in (
        ("alerts-to-log", "alert-log", "alertmanager", 9091),
        ("web-from-prometheus", "web", "prometheus", 8000),
    ):
        policy = indexed[("NetworkPolicy", name)]["spec"]
        if policy != {
            "podSelector": {"matchLabels": {"app": target}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {"kubernetes.io/metadata.name": "monitoring"}
                            },
                            "podSelector": {"matchLabels": {"app.kubernetes.io/name": source}},
                        }
                    ],
                    "ports": [{"protocol": "TCP", "port": port}],
                }
            ],
        }:
            raise ValueError("Monitoring access requires both namespace and pod selectors")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules-output", type=Path, help="Write rendered rules for promtool")
    args = parser.parse_args()
    pins = json.loads((ROOT / "deploy/platform/monitoring-schemas.json").read_text())
    schemas = {}
    for kind, pin in pins.items():
        with urllib.request.urlopen(pin["url"], timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != pin["sha256"]:
            raise ValueError(f"Monitoring schema checksum mismatch: {kind}")
        crd = yaml.safe_load(data)
        schema = next(
            v["schema"]["openAPIV3Schema"] for v in crd["spec"]["versions"] if v["name"] == "v1"
        )
        reject_unknown_fields(schema)
        jsonschema.Draft7Validator.check_schema(schema)
        schemas[kind] = jsonschema.Draft7Validator(schema)
    with tempfile.TemporaryDirectory(prefix="eps-monitoring-check-") as directory:
        for production, namespace in ((False, "eps"), (True, "eps"), (False, "eps-check")):
            command = [
                "helm",
                "template",
                "eps",
                str(ROOT / "deploy/helm/eps"),
                "--namespace",
                namespace,
                "--set",
                "monitoring.enabled=true",
            ]
            if production:
                for filename in ("values-production.yaml", "values-gitops.yaml"):
                    command += ["-f", str(ROOT / "deploy/helm/eps" / filename)]
                command += ["--set", "image.webDigest=sha256:" + "a" * 64]
            objects = [
                o for o in yaml.safe_load_all(subprocess.check_output(command, text=True)) if o
            ]
            validate_objects(objects)
            validate_monitoring(objects, namespace)
            if production:
                validate_gitops(objects)
            core = []
            for obj in objects:
                if obj["kind"] in schemas:
                    schemas[obj["kind"]].validate(obj)
                    # Exercise strict checking against the actual upstream schema.
                    obj["spec"]["invalidField"] = True
                    if not list(schemas[obj["kind"]].iter_errors(obj)):
                        raise ValueError("Unknown CRD fields were accepted")
                    del obj["spec"]["invalidField"]
                else:
                    core.append(obj)
            dashboards = next(o for o in objects if o["metadata"]["name"] == "eps-dashboards")
            queries = []
            for data in dashboards["data"].values():
                dashboard = json.loads(data)
                for panel in dashboard["panels"]:
                    for target in panel["targets"]:
                        expression = target["expr"]
                        if (
                            f'namespace="{namespace}"' not in expression
                            or "__EPS_NAMESPACE__" in expression
                        ):
                            raise ValueError("Dashboard query namespace does not match the release")
                        queries.append(expression)
            rules = next(o for o in objects if o["kind"] == "PrometheusRule")["spec"]
            if args.rules_output and production:
                args.rules_output.mkdir(parents=True, exist_ok=True)
                (args.rules_output / "alerts.yaml").write_text(
                    yaml.safe_dump(rules), encoding="utf-8"
                )
                query_rules = {
                    "groups": [
                        {
                            "name": "dashboard-validation",
                            "rules": [
                                {"record": f"eps_dashboard_check_{index}", "expr": expr}
                                for index, expr in enumerate(queries)
                            ],
                        }
                    ]
                }
                (args.rules_output / "dashboards.yaml").write_text(
                    yaml.safe_dump(query_rules), encoding="utf-8"
                )
            target = Path(directory) / "core.yaml"
            target.write_text(yaml.safe_dump_all(core), encoding="utf-8")
            subprocess.run(
                [
                    "kubeconform",
                    "-strict",
                    "-summary",
                    "-kubernetes-version",
                    "1.36.3",
                    str(target),
                ],
                check=True,
            )
            print(f"Monitoring passed: production={production}, namespace={namespace}")
    print("Static checks only; platform installation and live collection remain separate checks")


if __name__ == "__main__":
    main()
