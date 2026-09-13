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


def test_host_configuration_regressions_are_rejected():
    from pathlib import Path

    import yaml

    from scripts.check_deploy import validate_host_configs

    root = Path(__file__).resolve().parents[1]
    documents = [
        yaml.safe_load((root / path).read_text())
        for path in (
            "deploy/k3d.yaml",
            "deploy/platform/k3s-config.yaml",
            "deploy/platform/traefik.yaml",
        )
    ]
    validate_host_configs(*documents)
    for index, change in (
        (0, {"kubeAPI": {"hostIP": "0.0.0.0"}}),
        (0, {"ports": [{"port": "8080:80"}]}),
        (1, {"secrets-encryption": False}),
        (1, {"write-kubeconfig-mode": "0644"}),
        (1, {"disable": []}),
    ):
        changed = deepcopy(documents)
        changed[index].update(change)
        with pytest.raises(ValueError, match="Unsafe cluster"):
            validate_host_configs(*changed)
    for ingress_change in (
        {"service": {"type": "LoadBalancer"}},
        {"service": {"type": "ClusterIP", "externalIPs": ["192.0.2.1"]}},
        {"hostNetwork": True},
        {"ports": {"web": {"hostPort": 80}}},
        {"logs": {"access": {"enabled": True}}},
    ):
        changed = deepcopy(documents)
        values = yaml.safe_load(changed[2]["spec"]["valuesContent"])
        values.update(ingress_change)
        changed[2]["spec"]["valuesContent"] = yaml.safe_dump(values)
        with pytest.raises(ValueError, match="Traefik"):
            validate_host_configs(*changed)


def test_tracing_egress_rejects_broad_clients():
    from copy import deepcopy

    import pytest

    from scripts.check_deploy import validate_tracing

    web: dict = {
        "kind": "Deployment",
        "metadata": {"name": "web"},
        "spec": {"template": {"spec": {"containers": [{"env": []}]}}},
    }
    validate_tracing([web], False)
    web["spec"]["template"]["spec"]["containers"][0]["env"] = [
        {"name": "TRACING_ENABLED", "value": "true"},
        {"name": "TRACING_SAMPLE_RATE", "value": "0.1"},
    ]
    policy: dict = {
        "kind": "NetworkPolicy",
        "metadata": {"name": "web-to-tempo"},
        "spec": {
            "podSelector": {"matchLabels": {"app": "web"}},
            "policyTypes": ["Egress"],
            "egress": [
                {
                    "to": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {"kubernetes.io/metadata.name": "monitoring"}
                            },
                            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "tempo"}},
                        }
                    ],
                    "ports": [{"protocol": "TCP", "port": 4318}],
                }
            ],
        },
    }
    validate_tracing([web, policy], True)
    with pytest.raises(ValueError):
        validate_tracing([web, policy], False)
    for key in ("namespaceSelector", "podSelector"):
        changed = deepcopy(policy)
        changed["spec"]["egress"][0]["to"][0][key] = {}
        with pytest.raises(ValueError):
            validate_tracing([web, changed], True)


def test_request_scaling_has_one_owner_and_requires_monitoring():
    import shutil
    import subprocess

    import pytest
    import yaml

    from scripts.check_deploy import ROOT

    if not shutil.which("helm"):
        pytest.skip("Helm is required")
    command = ["helm", "template", "eps", str(ROOT / "deploy/helm/eps"), "-n", "eps-check"]
    for extra, count in [
        ([], 1),
        (["--set", "hpa.requestsPerSecond=0.5,monitoring.enabled=true"], 2),
    ]:
        objects = [o for o in yaml.safe_load_all(subprocess.check_output(command + extra)) if o]
        owners = [o for o in objects if o["kind"] in {"ScaledObject", "HorizontalPodAutoscaler"}]
        assert len(owners) == 1 and owners[0]["kind"] == "HorizontalPodAutoscaler"
        metrics = owners[0]["spec"]["metrics"]
        assert len(metrics) == count
        if count == 2:
            assert metrics[1]["pods"]["metric"]["name"] == "eps_http_requests_per_second"
            assert metrics[1]["pods"]["target"]["averageValue"] == "0.5"
    assert (
        subprocess.run(
            command + ["--set", "hpa.requestsPerSecond=1"], capture_output=True, check=False
        ).returncode
        != 0
    )
