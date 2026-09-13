"""Validate pinned platform charts and custom resources without a cluster."""

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "deploy/platform"

# Kubernetes CRDs may contain an unquoted '=' enum, tagged by YAML 1.1.
yaml.SafeLoader.add_constructor("tag:yaml.org,2002:value", lambda loader, node: node.value)


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


def validate_eso_role_extensions(obj: dict) -> None:
    labels = obj.get("metadata", {}).get("labels", {})
    if (
        any(
            key.startswith("rbac.authorization.k8s.io/aggregate-to-")
            and str(value).lower() == "true"
            for key, value in labels.items()
        )
        or str(labels.get("servicebinding.io/controller", "false")).lower() == "true"
    ):
        raise ValueError("Unexpected ESO default-role or service-binding permissions")


def validate_eso_scope(objects: list[dict], namespace: str) -> None:
    for obj in objects:
        if obj["kind"] == "Deployment":
            pod = obj["spec"]["template"]["spec"]
            if pod.get("imagePullSecrets") != [{"name": "dhi-pull"}] or any(
                not c["image"].startswith("dhi.io/external-secrets:2.10.0@sha256:")
                for c in pod["containers"]
            ):
                raise ValueError("ESO must use reviewed digest images and private registry access")
        if obj["kind"] in {"ClusterRole", "ClusterRoleBinding"}:
            raise ValueError("ESO must not receive cluster-wide RBAC")
        if obj["kind"] == "Role":
            for rule in obj.get("rules", []):
                if (
                    "secrets" in rule.get("resources", [])
                    and obj["metadata"].get("namespace") != namespace
                ):
                    raise ValueError("ESO Secret permissions escaped its namespace")
                if "serviceaccounts/token" in rule.get("resources", []):
                    raise ValueError("Chart must not grant unrestricted token creation")
    controller = next(
        o
        for o in objects
        if o["kind"] == "Deployment" and o["metadata"]["name"] == f"external-secrets-{namespace}"
    )
    args = controller["spec"]["template"]["spec"]["containers"][0]["args"]
    if f"--namespace={namespace}" not in args:
        raise ValueError("ESO controller must watch only its target namespace")


def validate_federation(identities: list[dict], stores: list[dict]) -> None:
    indexed = {(o["kind"], o["metadata"]["namespace"]): o for o in identities}
    for namespace in ("eps", "monitoring"):
        account = indexed[("ServiceAccount", namespace)]
        if (
            account["metadata"]["name"] != "eps-secrets"
            or account.get("automountServiceAccountToken") is not False
        ):
            raise ValueError("Federation identity must not automount tokens")
        role = indexed[("Role", namespace)]
        expected = [
            {
                "apiGroups": [""],
                "resources": ["serviceaccounts/token"],
                "resourceNames": ["eps-secrets"],
                "verbs": ["create"],
            }
        ]
        binding = indexed[("RoleBinding", namespace)]
        if (
            role["rules"] != expected
            or binding["subjects"]
            != [
                {
                    "kind": "ServiceAccount",
                    "name": f"external-secrets-{namespace}",
                    "namespace": "external-secrets",
                }
            ]
            or binding["roleRef"]
            != {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": role["metadata"]["name"],
            }
        ):
            raise ValueError("Federation token permissions exceed the intended identity")
        store = next(o for o in stores if o["metadata"]["namespace"] == namespace)
        provider = store["spec"]["provider"]["gcpsm"]
        auth = provider.get("auth", {})
        federation = auth.get("workloadIdentityFederation", {})
        if (
            set(auth) != {"workloadIdentityFederation"}
            or set(federation) != {"audience", "serviceAccountRef"}
            or federation["serviceAccountRef"]
            != {
                "name": "eps-secrets",
                "audiences": ["https:" + federation["audience"]],
            }
            or not federation["audience"].startswith("//iam.googleapis.com/projects/")
            or not provider.get("projectID")
        ):
            raise ValueError("SecretStore must explicitly use the scoped federation identity")


def validate_monitoring_access() -> None:
    """Keep exporter credentials and database access in the application namespace."""
    secrets = list(yaml.safe_load_all((PLATFORM / "external-secrets.yaml").read_text()))
    exporters = [o for o in secrets if o["metadata"]["name"] == "eps-exporter"]
    if len(exporters) != 1 or exporters[0]["metadata"]["namespace"] != "eps":
        raise ValueError("Database exporter credentials must stay in eps")
    policies = list(yaml.safe_load_all((PLATFORM / "exporter-networkpolicies.yaml").read_text()))
    if {p["metadata"]["name"] for p in policies} != {
        "db-from-exporter",
        "exporter-from-prometheus",
        "exporter-to-db",
    } or len(policies) != 3:
        raise ValueError("Expected exactly three exporter network policies")
    exporter = {"app.kubernetes.io/name": "prometheus-postgres-exporter"}
    for policy in policies:
        name = policy["metadata"]["name"]
        target = {"app": "db"} if name == "db-from-exporter" else exporter
        peer = {
            "podSelector": {
                "matchLabels": exporter if name == "db-from-exporter" else {"app": "db"}
            }
        }
        if name == "exporter-from-prometheus":
            peer = {
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}},
            }
        direction = "egress" if name == "exporter-to-db" else "ingress"
        expected = {
            "podSelector": {"matchLabels": target},
            "policyTypes": [direction.title()],
            direction: [
                {
                    "to" if direction == "egress" else "from": [peer],
                    "ports": [
                        {
                            "protocol": "TCP",
                            "port": 9187 if name == "exporter-from-prometheus" else 5432,
                        }
                    ],
                }
            ],
        }
        if policy["metadata"]["namespace"] != "eps" or policy["spec"] != expected:
            raise ValueError("Exporter network policy scope changed")


def validate_monitoring_platform(objects: list[dict], chart: str) -> None:
    """Reject public services, unpinned images and expanded Grafana permissions."""
    for obj in objects:
        kind, name = obj["kind"], obj["metadata"]["name"]
        if kind == "Ingress" or kind == "DaemonSet":
            raise ValueError("Monitoring must not expose ingress or run host collectors")
        if kind == "Service" and (
            obj["spec"].get("type", "ClusterIP") != "ClusterIP" or obj["spec"].get("externalIPs")
        ):
            raise ValueError("Monitoring Services must stay internal")
        if (
            kind in {"Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding"}
            and "grafana" in name
        ):
            raise ValueError("Grafana must not receive Kubernetes API permissions")
        if kind in {"Prometheus", "Alertmanager"}:
            spec = obj["spec"]
            if not spec.get("image", "").startswith("dhi.io/") or "@sha256:" not in spec["image"]:
                raise ValueError("Monitoring runtime images must be digest-pinned")
            if spec.get("imagePullSecrets") != [{"name": "dhi-pull"}]:
                raise ValueError("Monitoring registry access is missing")
            if kind == "Prometheus" and (
                spec.get("arbitraryFSAccessThroughSMs") != {"deny": True}
                or spec.get("overrideHonorLabels") is not True
            ):
                raise ValueError("Prometheus scrape credentials or label boundary changed")
        if kind not in {"Deployment", "StatefulSet"}:
            continue
        pod = obj["spec"]["template"]["spec"]
        if any(pod.get(key) for key in ("hostNetwork", "hostPID", "hostIPC")) or any(
            "hostPath" in v for v in (pod.get("volumes") or [])
        ):
            raise ValueError("Monitoring pods must not access the host")
        if pod.get("securityContext", {}).get("runAsNonRoot") is not True:
            raise ValueError("Monitoring pods must run as non-root")
        account = next(
            (
                o
                for o in objects
                if o["kind"] == "ServiceAccount"
                and o["metadata"]["name"] == pod.get("serviceAccountName")
            ),
            {},
        )
        if pod.get("imagePullSecrets", account.get("imagePullSecrets")) != [{"name": "dhi-pull"}]:
            raise ValueError("Monitoring pod registry access is missing")
        if (
            chart in {"loki", "tempo"}
            and pod.get("automountServiceAccountToken", account.get("automountServiceAccountToken"))
            is not False
        ):
            raise ValueError("Telemetry storage must not mount Kubernetes API tokens")
        if name == "grafana" and (
            pod.get("automountServiceAccountToken") is not False
            or len(pod["containers"]) != 1
            or pod.get("initContainers")
        ):
            raise ValueError("Grafana must use native provisioning without sidecars or API tokens")
        for container in (pod.get("initContainers") or []) + pod["containers"]:
            security = container.get("securityContext", {})
            if not container["image"].startswith("dhi.io/") or "@sha256:" not in container["image"]:
                raise ValueError("Monitoring pod images must be digest-pinned")
            if (
                security.get("allowPrivilegeEscalation") is not False
                or "ALL" not in security.get("capabilities", {}).get("drop", [])
                or security.get("readOnlyRootFilesystem") is not True
            ):
                raise ValueError("Monitoring container security changed")
        if chart == "prometheus-postgres-exporter" and (
            pod.get("automountServiceAccountToken") is not False
            or pod["containers"][0].get("env")
            != [
                {
                    "name": "DATA_SOURCE_NAME",
                    "valueFrom": {
                        "secretKeyRef": {"name": "eps-exporter", "key": "DATA_SOURCE_NAME"}
                    },
                }
            ]
        ):
            raise ValueError(
                "Exporter must use only its existing database Secret without an API token"
            )


def validate_telemetry(objects: list[dict], chart: str) -> None:
    """Constrain the collector's API access and the storage receivers."""
    validate_monitoring_platform(objects, chart)
    for obj in objects:
        kind = obj["kind"]
        if kind in {"Pod", "Job", "CronJob", "Secret"}:
            raise ValueError("Unexpected telemetry workload or credential")
        if kind in {"ClusterRole", "ClusterRoleBinding"}:
            raise ValueError("Telemetry must not receive cluster-wide RBAC")
        if kind in {"Role", "RoleBinding"}:
            if chart != "alloy" or obj["metadata"].get("namespace") != "eps":
                raise ValueError("Only Alloy may receive telemetry RBAC, in eps")
            if kind == "Role" and obj["rules"] != [
                {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]},
                {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
            ]:
                raise ValueError("Alloy permissions must be limited to reading EPS pod logs")
            if kind == "RoleBinding" and (
                obj["subjects"]
                != [{"kind": "ServiceAccount", "name": "alloy", "namespace": "monitoring"}]
                or obj["roleRef"]
                != {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "alloy"}
            ):
                raise ValueError("Alloy binding must target only its monitoring identity")
        if kind == "StatefulSet" and any(
            v == "Delete"
            for v in obj["spec"].get("persistentVolumeClaimRetentionPolicy", {}).values()
        ):
            raise ValueError("Telemetry PVCs must survive workload removal")
        if kind == "ConfigMap" and chart == "tempo" and "tempo.yaml" in obj.get("data", {}):
            config = yaml.safe_load(obj["data"]["tempo.yaml"])
            receivers = config["distributor"]["receivers"]
            if receivers != {"otlp": {"protocols": {"http": {"endpoint": "0.0.0.0:4318"}}}}:
                raise ValueError("Tempo must enable only the OTLP HTTP receiver")


def validate_telemetry_network(objects: list[dict]) -> None:
    """Check the exact namespace, client selectors and ports for private storage."""
    expected_clients = {
        "alloy": [],
        "loki": [("monitoring", "alloy", 3100), ("monitoring", "grafana", 3100)],
        "tempo": [("eps", "web", 4318), ("monitoring", "grafana", 3200)],
    }
    if len(objects) != 3:
        raise ValueError("Expected three telemetry ingress policies")
    for obj in objects:
        name = obj["metadata"]["name"].removesuffix("-ingress")
        spec = obj["spec"]
        if (
            name not in expected_clients
            or obj["metadata"].get("namespace") != "monitoring"
            or spec["podSelector"] != {"matchLabels": {"app.kubernetes.io/name": name}}
            or spec["policyTypes"] != ["Ingress"]
        ):
            raise ValueError("Telemetry policy target changed")
        clients = []
        for rule in spec["ingress"]:
            for peer in rule["from"]:
                for port in rule["ports"]:
                    namespace = peer.get(
                        "namespaceSelector",
                        {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                    )
                    ns = namespace.get("matchLabels", {}).get("kubernetes.io/metadata.name")
                    label = "app" if ns == "eps" else "app.kubernetes.io/name"
                    pod = peer.get("podSelector", {})
                    value = pod.get("matchLabels", {}).get(label)
                    if (
                        namespace != {"matchLabels": {"kubernetes.io/metadata.name": ns}}
                        or pod != {"matchLabels": {label: value}}
                        or set(peer) - {"namespaceSelector", "podSelector"}
                        or port != {"protocol": "TCP", "port": port.get("port")}
                    ):
                        raise ValueError("Telemetry client selector changed")
                    clients.append((ns, value, port["port"]))
        if sorted(clients) != sorted(expected_clients.pop(name)):
            raise ValueError("Telemetry ingress must allow only the intended clients and ports")


def dashboard_configmap() -> dict:
    """Mount the existing dashboard sources without a collector or duplicate JSON."""
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "eps-grafana-dashboards", "namespace": "monitoring"},
        "data": {
            path.name: path.read_text(encoding="utf-8").replace("__EPS_NAMESPACE__", "eps")
            for path in sorted((ROOT / "deploy/helm/eps/dashboards").glob("*.json"))
        },
    }


def main() -> None:
    pins = json.loads((PLATFORM / "versions.json").read_text(encoding="utf-8"))
    identities = list(yaml.safe_load_all((PLATFORM / "eso-identities.yaml").read_text()))
    stores = list(yaml.safe_load_all((PLATFORM / "secret-store.yaml.example").read_text()))
    validate_federation(identities, stores)
    validate_monitoring_access()
    validate_telemetry_network(
        list(yaml.safe_load_all((PLATFORM / "telemetry-network.yaml").read_text()))
    )
    schemas = {}
    core = [dashboard_configmap()]
    custom: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="eps-platform-check-") as temporary:
        work = Path(temporary)
        for name, pin in pins.items():
            subprocess.run(
                [
                    "helm",
                    "pull",
                    name,
                    "--repo",
                    pin["repository"],
                    "--version",
                    pin["version"],
                    "--destination",
                    str(work),
                ],
                check=True,
            )
            archive = work / f"{name}-{pin['version']}.tgz"
            if hashlib.sha256(archive.read_bytes()).hexdigest() != pin["sha256"]:
                raise ValueError(f"Chart checksum mismatch: {name}")
            releases = (
                [(pin.get("release", name), None)]
                if name != "external-secrets"
                else [
                    ("external-secrets-eps", "eps"),
                    ("external-secrets-monitoring", "monitoring"),
                ]
            )
            for release, namespace in releases:
                args = [
                    "helm",
                    "template",
                    release,
                    str(archive),
                    "--namespace",
                    pin.get("namespace", name),
                    "--include-crds",
                    "-f",
                    str(PLATFORM / f"{name}-values.yaml"),
                ]
                if namespace:
                    args += ["-f", str(PLATFORM / f"{release}-values.yaml")]
                output = subprocess.check_output(args, text=True, encoding="utf-8")
                objects = [obj for obj in yaml.safe_load_all(output) if obj]
                if namespace:
                    validate_eso_scope(objects, namespace)
                if name in {"kube-prometheus-stack", "prometheus-postgres-exporter"}:
                    validate_monitoring_platform(objects, name)
                if name in {"alloy", "loki", "tempo"}:
                    validate_telemetry(objects, name)
                for obj in objects:
                    if name == "external-secrets":
                        validate_eso_role_extensions(obj)
                    if obj["kind"] == "CustomResourceDefinition":
                        spec = obj["spec"]
                        for version in spec["versions"]:
                            schemas[
                                (spec["group"] + "/" + version["name"], spec["names"]["kind"])
                            ] = version["schema"]["openAPIV3Schema"]
                    else:
                        core.append(obj)
        # Validate the instantiated CRD versions; admission and CEL require a cluster.
        for filename in (
            "namespaces.yaml",
            "metadata-policy.yaml",
            "eso-identities.yaml",
            "exporter-networkpolicies.yaml",
            "telemetry-network.yaml",
        ):
            core.extend(o for o in yaml.safe_load_all((PLATFORM / filename).read_text()) if o)
        for filename in (
            "external-secrets.yaml",
            "secret-store.yaml.example",
            "eso-webhook-issuer.yaml",
        ):
            custom.extend(o for o in yaml.safe_load_all((PLATFORM / filename).read_text()) if o)
        custom += [obj for obj in core if (obj["apiVersion"], obj["kind"]) in schemas]
        core = [obj for obj in core if (obj["apiVersion"], obj["kind"]) not in schemas]
        for key in {(obj["apiVersion"], obj["kind"]) for obj in custom}:
            reject_unknown_fields(schemas[key])
            jsonschema.Draft7Validator.check_schema(schemas[key])
        for obj in custom:
            schema = schemas[(obj["apiVersion"], obj["kind"])]
            jsonschema.Draft7Validator(schema).validate(obj)
        target = work / "core.yaml"
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
    print(f"Validated {len(custom)} custom resources against pinned chart schemas")
    print("Static checks only; image/RBAC/authentication gates and live checks remain open")


if __name__ == "__main__":
    main()
