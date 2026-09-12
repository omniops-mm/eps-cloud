"""ESO must not silently extend default user roles or integration permissions."""

import pytest

from scripts.check_platform import validate_eso_role_extensions


def test_role_extensions_rejected():
    validate_eso_role_extensions({"metadata": {"name": "external-secrets-controller"}})
    for label in (
        "rbac.authorization.k8s.io/aggregate-to-view",
        "rbac.authorization.k8s.io/aggregate-to-edit",
        "rbac.authorization.k8s.io/aggregate-to-admin",
        "servicebinding.io/controller",
    ):
        validate_eso_role_extensions({"metadata": {"labels": {label: "false"}}})
        with pytest.raises(ValueError, match="Unexpected ESO"):
            validate_eso_role_extensions({"metadata": {"labels": {label: "true"}}})
