"""Keep the Ansible platform path tied to the reviewed platform inputs."""

import importlib.util
import json
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).parents[1]
ROLE = ROOT / "infra" / "ansible" / "roles" / "platform" / "tasks" / "main.yaml"
PINS = ROOT / "deploy" / "platform" / "versions.json"


def test_platform_role_installs_every_pinned_runtime_chart_without_registry_login():
    role = ROLE.read_text(encoding="utf-8")
    pins = json.loads(PINS.read_text(encoding="utf-8"))

    tasks = yaml.safe_load(role)
    checks = next(
        task["loop"] for task in tasks if task["name"] == "Verify reviewed chart archive checksums."
    )
    for chart in pins:
        assert f"chart: {chart}" in role
        checksum = next(item["sha256"] for item in checks if item["chart"] == chart)
        assert (
            f"platform_pins['{chart}'].sha256" in checksum
            or f"platform_pins.{chart}.sha256" in checksum
        )

    assert "chart: argo-cd" in role
    assert "platform_argo_pin.digest" in role
    assert "include_tasks: install_chart.yaml" in role
    assert "KUBECONFIG: /etc/rancher/k3s/k3s.yaml" in (
        ROOT / "infra" / "ansible" / "roles" / "platform" / "tasks" / "install_chart.yaml"
    ).read_text(encoding="utf-8")
    helper = (
        ROOT / "infra" / "ansible" / "roles" / "platform" / "files" / "merge_helm_values.py"
    ).read_text(encoding="utf-8")
    assert "imagePullPolicy" in helper
    assert "pullPolicy" in helper
    assert "docker login" not in role
    assert "registry_password" not in role


def test_chart_templates_resolve_values_keys_instead_of_dict_methods():
    environment = Environment(undefined=StrictUndefined)
    tasks = yaml.safe_load(ROLE.read_text(encoding="utf-8"))
    monitoring = next(
        task for task in tasks if task["name"] == "Install the pinned monitoring releases."
    )
    item = monitoring["loop"][0]
    value_file = environment.from_string(
        monitoring["vars"]["platform_chart_spec"]["values"][0]
    ).render(item=item)
    assert value_file == item["values"]

    chart_tasks = yaml.safe_load(ROLE.with_name("install_chart.yaml").read_text(encoding="utf-8"))
    command = environment.from_string(chart_tasks[0]["ansible.builtin.command"]["cmd"]).render(
        ansible_playbook_python="python3",
        role_path="/role",
        playbook_dir="/infra/ansible",
        platform_chart_spec={"local_images": True, "values": [value_file]},
    )
    assert "--local-images" in command
    assert "/deploy/platform/" + value_file in command


def test_merge_helper_strips_nested_pull_secrets_and_pins_local_images():
    helper_path = (
        ROOT / "infra" / "ansible" / "roles" / "platform" / "files" / "merge_helm_values.py"
    )
    spec = importlib.util.spec_from_file_location("merge_helm_values", helper_path)
    assert spec is not None
    helper = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(helper)

    values = helper.strip_pull_secrets(
        {
            "global": {"imagePullSecrets": [{"name": "dhi-pull"}], "image": {}},
            "nested": {"pullSecrets": ["dhi-pull"], "image": {"repository": "dhi.io/app"}},
        },
        local_images=True,
    )

    assert "imagePullSecrets" not in values["global"]
    assert "pullSecrets" not in values["nested"]
    assert values["nested"]["image"]["pullPolicy"] == "Never"
