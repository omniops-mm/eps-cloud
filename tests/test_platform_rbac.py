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


def test_federation_boundaries():
    from copy import deepcopy
    from pathlib import Path

    import yaml

    from scripts.check_platform import validate_federation

    platform = Path(__file__).resolve().parents[1] / "deploy/platform"
    identities = list(yaml.safe_load_all((platform / "eso-identities.yaml").read_text()))
    stores = list(yaml.safe_load_all((platform / "secret-store.yaml.example").read_text()))
    validate_federation(identities, stores)
    changed = deepcopy(identities)
    role = next(o for o in changed if o["kind"] == "Role")
    role["rules"][0]["resourceNames"] = ["default"]
    with pytest.raises(ValueError, match="token permissions"):
        validate_federation(changed, stores)
    changed_stores = deepcopy(stores)
    del changed_stores[0]["spec"]["provider"]["gcpsm"]["auth"]
    with pytest.raises(ValueError, match="explicitly use"):
        validate_federation(identities, changed_stores)

    for audiences in (None, ["k3s"], ["https://wrong-provider.example"]):
        changed_stores = deepcopy(stores)
        account = changed_stores[0]["spec"]["provider"]["gcpsm"]["auth"][
            "workloadIdentityFederation"
        ]["serviceAccountRef"]
        if audiences is None:
            del account["audiences"]
        else:
            account["audiences"] = audiences
        with pytest.raises(ValueError, match="explicitly use"):
            validate_federation(identities, changed_stores)


def test_cluster_wide_eso_role_rejected():
    from scripts.check_platform import validate_eso_scope

    with pytest.raises(ValueError, match="cluster-wide"):
        validate_eso_scope([{"kind": "ClusterRole"}], "eps")
