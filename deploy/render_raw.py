"""Refresh the raw Kubernetes examples from the development Helm values."""

import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
rendered = subprocess.check_output(
    ["helm", "template", "eps", str(root / "helm/eps"), "--namespace", "eps"], text=True
)
names = {
    "postgres.yaml": "20-postgres.yaml",
    "web.yaml": "30-web.yaml",
    "cronjobs.yaml": "40-cronjobs.yaml",
    "ingress.yaml": "50-ingress.yaml",
    "networkpolicies.yaml": "60-networkpolicies.yaml",
    "hpa.yaml": "70-hpa.yaml",
}
grouped: dict[str, list[str]] = {name: [] for name in names}
for document in rendered.split("---\n"):
    if not document.strip():
        continue
    source = document.splitlines()[0].rsplit("/", 1)[-1]
    if source in grouped:
        grouped[source].append(document)
missing = [source for source, documents in grouped.items() if not documents]
if missing:
    raise RuntimeError(f"Helm omitted expected templates: {missing}")

for source, target in names.items():
    (root / "raw-manifests" / target).write_text(
        "---\n".join(grouped[source]).rstrip() + "\n", encoding="utf-8", newline="\n"
    )

# Keep the namespace admission rules aligned with the platform definition.
namespace = (root / "platform/namespaces.yaml").read_text(encoding="utf-8").split("---", 1)[0]
(root / "raw-manifests/00-namespace.yaml").write_text(namespace, encoding="utf-8", newline="\n")
