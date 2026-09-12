"""Security checks must reject container overrides as well as pod defaults."""

from copy import deepcopy

import pytest

from scripts.check_deploy import validate_objects


def test_security_regressions_are_rejected():
    pod: dict = {
        "automountServiceAccountToken": False,
        "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
        "containers": [
            {
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "readOnlyRootFilesystem": True,
                }
            }
        ],
    }

    def objects(spec):
        return [
            {
                "kind": "Deployment",
                "metadata": {"name": "web"},
                "spec": {"template": {"spec": spec}},
            }
        ]

    validate_objects(objects(pod))
    for field, value in (
        ("privileged", True),
        ("runAsUser", 0),
        ("allowPrivilegeEscalation", True),
        ("readOnlyRootFilesystem", False),
    ):
        changed = deepcopy(pod)
        changed["containers"][0]["securityContext"][field] = value
        with pytest.raises(ValueError, match="Unsafe container"):
            validate_objects(objects(changed))
    changed = deepcopy(pod)
    changed["automountServiceAccountToken"] = True
    with pytest.raises(ValueError, match="Unsafe pod"):
        validate_objects(objects(changed))
