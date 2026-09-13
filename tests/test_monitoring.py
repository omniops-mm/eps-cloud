"""Reject monitoring schema typos and expanded network or credential access."""

import subprocess
from copy import deepcopy

import jsonschema
import pytest
import yaml

from scripts.check_deploy import ROOT
from scripts.check_monitoring import reject_unknown_fields, validate_monitoring


def test_strict_schema_preserves_open_maps():
    schema: dict = {
        "type": "object",
        "properties": {
            "spec": {"type": "object", "properties": {"port": {"type": "integer"}}},
            "labels": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }
    schema["properties"]["strategy"] = {"type": "string", "pattern": "^(?i)(abort|warn)?$"}
    reject_unknown_fields(schema)
    jsonschema.Draft7Validator.check_schema(schema)
    validator = jsonschema.Draft7Validator(schema)
    validator.validate({"spec": {"port": 80}, "labels": {"app": "eps"}})
    validator.validate({"strategy": "WARN"})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"strategy": "ignore"})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"spec": {"port": 80, "prot": 90}})


def test_monitoring_boundary_mutations():
    # Helm is installed in the deployment CI job; application tests need no CLI tools.
    import shutil

    if not shutil.which("helm"):
        pytest.skip("Helm is required for rendered monitoring mutation checks")
    output = subprocess.check_output(
        [
            "helm",
            "template",
            "eps",
            str(ROOT / "deploy/helm/eps"),
            "--namespace",
            "eps",
            "--set",
            "monitoring.enabled=true",
        ],
        text=True,
    )
    objects = [o for o in yaml.safe_load_all(output) if o]
    validate_monitoring(objects, "eps")
    for kind, name, section, field, value in (
        ("Service", "alert-log", "spec", "type", "LoadBalancer"),
        ("ServiceMonitor", "eps-web", "spec", "namespaceSelector", {"any": True}),
        (
            "NetworkPolicy",
            "alerts-to-log",
            "spec",
            "ingress",
            [{"from": [{"namespaceSelector": {}}]}],
        ),
        ("NetworkPolicy", "web-from-prometheus", "spec", "ingress", [{}]),
    ):
        changed = deepcopy(objects)
        obj = next(o for o in changed if o["kind"] == kind and o["metadata"]["name"] == name)
        obj[section][field] = value
        with pytest.raises(ValueError):
            validate_monitoring(changed, "eps")
    changed = deepcopy(objects)
    receiver = next(
        o for o in changed if o["kind"] == "Deployment" and o["metadata"]["name"] == "alert-log"
    )
    receiver["spec"]["template"]["spec"]["containers"][0]["envFrom"] = [
        {"secretRef": {"name": "eps"}}
    ]
    with pytest.raises(ValueError, match="without credentials"):
        validate_monitoring(changed, "eps")
