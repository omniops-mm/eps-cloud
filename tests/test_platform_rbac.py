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


def test_monitoring_access_rejects_expanded_permissions(tmp_path, monkeypatch):
    from copy import deepcopy

    import yaml

    from scripts import check_platform

    files = (
        "grafana-dashboard-rbac.yaml",
        "external-secrets.yaml",
        "exporter-networkpolicies.yaml",
    )
    original = {
        name: list(yaml.safe_load_all((check_platform.PLATFORM / name).read_text()))
        for name in files
    }
    monkeypatch.setattr(check_platform, "PLATFORM", tmp_path)

    def write(documents):
        for name, objects in documents.items():
            (tmp_path / name).write_text(yaml.safe_dump_all(objects))

    write(original)
    check_platform.validate_monitoring_access()
    changed = deepcopy(original)
    changed[files[0]][0]["rules"][0]["resources"].append("secrets")
    write(changed)
    with pytest.raises(ValueError, match="ConfigMap-only"):
        check_platform.validate_monitoring_access()
    changed = deepcopy(original)
    changed[files[0]][1]["subjects"][0]["namespace"] = "default"
    write(changed)
    with pytest.raises(ValueError, match="ConfigMap-only"):
        check_platform.validate_monitoring_access()
    changed = deepcopy(original)
    exporter = next(o for o in changed[files[1]] if o["metadata"]["name"] == "eps-exporter")
    exporter["metadata"]["namespace"] = "monitoring"
    write(changed)
    with pytest.raises(ValueError, match="credentials must stay"):
        check_platform.validate_monitoring_access()
    changed = deepcopy(original)
    changed[files[2]][1]["spec"]["ingress"][0]["from"][0]["namespaceSelector"] = {}
    write(changed)
    with pytest.raises(ValueError, match="policy scope"):
        check_platform.validate_monitoring_access()


def test_monitoring_platform_rejects_public_access_and_secret_rbac():
    from scripts.check_platform import validate_monitoring_platform

    validate_monitoring_platform(
        [{"kind": "Service", "metadata": {"name": "grafana"}, "spec": {"type": "ClusterIP"}}],
        "kube-prometheus-stack",
    )
    for obj in (
        {"kind": "Service", "metadata": {"name": "grafana"}, "spec": {"type": "LoadBalancer"}},
        {
            "kind": "Service",
            "metadata": {"name": "grafana"},
            "spec": {"type": "ClusterIP", "externalIPs": ["192.0.2.1"]},
        },
        {
            "kind": "Role",
            "metadata": {"name": "grafana", "namespace": "monitoring"},
            "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]}],
        },
        {"kind": "ClusterRole", "metadata": {"name": "grafana"}, "rules": []},
        {"kind": "Ingress", "metadata": {"name": "grafana"}},
    ):
        with pytest.raises(ValueError):
            validate_monitoring_platform([obj], "kube-prometheus-stack")
