"""Build the v0.5 README diagrams using the existing EPS SVG helpers.

Run: python docs/diagrams/build_v05.py
"""

from html import escape
from pathlib import Path
from xml.etree import ElementTree

import build as d

OUT = Path(__file__).resolve().parents[1] / "img" / "v05"


def text(x, y, value, size=14, colour=d.MUTED, anchor="start"):
    return f'<text class="d" x="{x}" y="{y}" font-size="{size}" fill="{colour}" text-anchor="{anchor}">{escape(value)}</text>'


def heading(value):
    return text(32, 44, value, 25, d.TEXT)


def label(x, y, value):
    return text(x, y, value.upper(), 12, d.ACCENT)


def box(x, y, w, title, sub="", tone=None):
    body = d.card(x, y, w, 76, fill=d.RAISED, stroke=d.BORDER_STRONG, r=8)
    if tone:
        body += d.left_bar(x, y, w, 76, tone)
    body += text(x + 16, y + 29, title, 18, d.TEXT)
    if sub:
        body += text(x + 16, y + 55, sub)
    return body


def edge(path, tone=d.MUTED):
    return d.curve(path, tone)


def finish(title, desc, height, parts, width=1000):
    return d.svg(width, height, title, desc, "".join(parts))


def cluster():
    p = [heading("Kubernetes workloads"), d.card(285, 86, 685, 596)]
    p += [label(307, 115, "k3s node"), label(560, 150, "eps namespace")]
    p += [d.card(540, 162, 408, 352, fill=d.BG)]
    p += [box(30, 200, 205, "Operator", "Browser + tunnel", d.ACCENT)]
    p += [box(307, 200, 190, "Traefik", "HTTPS ingress")]
    p += [box(565, 200, 355, "Web Deployment", "Flask + gunicorn")]
    p += [box(565, 321, 160, "CronJobs", "Scheduled jobs")]
    p += [box(760, 321, 160, "PostgreSQL", "StatefulSet")]
    p += [box(760, 424, 160, "PVC", "Database files", d.OK)]
    p += [edge("M235,238 H298", d.ACCENT), edge("M497,238 H556", d.ACCENT)]
    p += [edge("M840,276 V312"), edge("M725,359 H751"), edge("M840,397 V415")]
    p += [label(307, 327, "kube-system"), text(307, 357, "Ingress controller")]
    p += [label(307, 554, "Platform controllers")]
    for x, title, sub in [
        (307, "cert-manager", "TLS certificates"),
        (528, "External Secrets", "Credential delivery"),
        (749, "Argo CD", "Application sync"),
    ]:
        p += [box(x, 578, 199, title, sub)]
    return finish(
        "EPS Kubernetes workloads",
        "Private HTTPS passes through Traefik to the web Deployment. Web pods and CronJobs connect to PostgreSQL, backed by a PVC. Platform controllers provide certificates, credentials and application synchronization.",
        704,
        p,
    )


def observability():
    p = [heading("Cluster telemetry")]
    for x, title in [(32, "Source"), (262, "Collection"), (522, "Storage"), (790, "Explore")]:
        p += [label(x, 101, title)]
    rows = [
        (
            130,
            "Metrics",
            "App + exporters",
            "ServiceMonitors",
            "Scrape configuration",
            "Prometheus",
        ),
        (280, "JSON logs", "Web + jobs", "Alloy", "Kubernetes logs", "Loki"),
        (430, "Request spans", "OpenTelemetry", "OTLP / HTTP", "Direct app export", "Tempo"),
    ]
    for y, source, sub, collector, detail, store in rows:
        p += [
            box(32, y, 170, source, sub),
            box(262, y, 200, collector, detail),
            box(522, y, 190, store, "Persistent volume", d.ACCENT),
        ]
        p += [edge(f"M202,{y + 38} H253"), edge(f"M462,{y + 38} H513")]
    p += [box(790, 280, 180, "Grafana", "Query + correlate", d.OK)]
    p += [edge("M790,318 H721"), edge("M750,318 V168 H721"), edge("M750,318 V468 H721")]
    return finish(
        "EPS metrics, logs and traces",
        "Prometheus scrapes targets selected by ServiceMonitors. Alloy forwards container logs to Loki. The app exports traces directly to Tempo. Grafana queries the three stores.",
        545,
        p,
    )


def telemetry_workflows():
    p = [heading("Request correlation in Grafana")]
    for x, title, sub in [
        (32, "Metric exemplar", "Request measurement"),
        (365, "Tempo trace", "Request + database spans"),
        (698, "Loki logs", "Matching request events"),
    ]:
        p += [box(x, 126, 270, title, sub)]
    p += [edge("M302,164 H356"), edge("M635,164 H689")]
    p += [
        text(332, 112, "trace ID", 13, anchor="middle"),
        text(666, 112, "trace ID", 13, anchor="middle"),
    ]
    return finish(
        "Request correlation",
        "Grafana follows an exemplar trace ID to Tempo and correlates spans with Loki events. Logs also link directly to their trace.",
        238,
        p,
    )


def alerts():
    p = [heading("Alert delivery")]
    for x, title, sub in [
        (32, "Prometheus rules", "Evaluate conditions"),
        (365, "Alertmanager", "Group + route"),
        (698, "Webhook receiver", "Record notifications"),
    ]:
        p += [box(x, 126, 270, title, sub)]
    p += [edge("M302,164 H356"), edge("M635,164 H689")]
    p += [
        text(332, 112, "alerts", 13, anchor="middle"),
        text(666, 112, "webhook", 13, anchor="middle"),
    ]
    return finish(
        "Alert delivery",
        "Prometheus evaluates rules, Alertmanager groups alerts, and the internal receiver records firing and resolved notifications.",
        238,
        p,
    )


def delivery():
    p = [heading("CI/CD and GitOps")]
    p += [d.card(24, 86, 952, 250), d.card(370, 420, 606, 220)]
    p += [
        text(44, 118, "GitHub · source and deployment branches", 17, d.ACCENT),
        text(394, 452, "Google Cloud · k3s", 17, d.ACCENT),
    ]
    p += [
        box(44, 180, 210, "master", "Source code"),
        box(394, 180, 210, "CI + snapshot", "Test · build · sign"),
        box(744, 180, 210, "production", "Chart + digests", d.ACCENT),
    ]
    p += [edge("M254,218 H385"), text(324, 164, "push triggers CI", 15, anchor="middle")]
    p += [edge("M604,218 H735"), text(674, 164, "review + promote", 15, anchor="middle")]
    p += [
        box(44, 478, 210, "GHCR", "Signed images"),
        box(394, 478, 210, "Workloads", "Web · database · jobs"),
        box(744, 478, 210, "Argo CD", "Reconciliation", d.ACCENT),
    ]
    p += [
        edge("M499,256 V302 H149 V469"),
        text(169, 382, "publish", 16),
        edge("M849,478 V265", d.ACCENT),
        text(868, 382, "read Git", 16),
        edge("M254,516 H385", d.OK),
        text(311, 460, "verify + import", 14, d.OK, "middle"),
        edge("M744,497 H613", d.ACCENT),
        text(674, 474, "sync", 16, anchor="middle"),
        edge("M604,536 H735"),
        text(674, 582, "observe", 16, anchor="middle"),
    ]
    return finish(
        "EPS delivery pipeline",
        "Local hooks precede a push to master. GitHub Actions validates and signs images, publishes to GHCR, and packages a snapshot. The operator promotes it to production. On GCP, Argo CD reads desired state from Git, observes Kubernetes state and applies changes on requested synchronization. Verified image imports supply the node separately.",
        666,
        p,
    )


def secrets():
    p = [heading("Workload identity and secret delivery")]
    for x, title, sub in [
        (32, "Service account", "Kubernetes identity"),
        (365, "Federation", "Short-lived access token"),
        (698, "Secret Manager", "Per-secret IAM grant"),
    ]:
        p += [box(x, 113, 270, title, sub)]
    p += [edge("M302,151 H356"), edge("M635,151 H689")]
    for x, title, sub in [
        (32, "Workloads", "Assigned credentials"),
        (365, "Kubernetes Secret", "Encrypted at rest"),
        (698, "External Secrets", "Namespace-scoped"),
    ]:
        p += [box(x, 283, 270, title, sub)]
    p += [edge("M833,189 V274"), text(849, 239, "read", 14)]
    p += [edge("M698,321 H644"), edge("M365,321 H311")]
    return finish(
        "Workload secret delivery",
        "External Secrets uses named Kubernetes service accounts and federation to read permitted Secret Manager values, then refreshes namespace-local Kubernetes Secrets.",
        393,
        p,
    )


def security():
    p = [heading("Selected NetworkPolicy paths")]
    p += [d.card(24, 88, 693, 310), text(44, 120, "EPS namespace", 17, d.ACCENT)]
    p += [d.card(746, 88, 230, 310), text(764, 120, "Monitoring", 17, d.ACCENT)]
    p += [
        box(44, 285, 170, "PostgreSQL", "Database"),
        box(288, 285, 170, "Job pods", "Workers"),
        box(526, 285, 170, "Web pods", "Application"),
        box(764, 158, 194, "Prometheus", "Metrics"),
        box(764, 285, 194, "Tempo", "Traces"),
    ]
    p += [
        edge("M288,323 H223"),
        text(250, 274, "SQL", 15, anchor="middle"),
        edge("M611,285 V204 H129 V276"),
        text(366, 190, "SQL", 15, anchor="middle"),
        edge("M764,196 H669 V276"),
        text(676, 166, "scrape", 15, anchor="middle"),
        edge("M696,323 H755"),
        text(786, 274, "spans", 15),
    ]
    return finish(
        "Selected workload network permissions",
        "NetworkPolicies allow selected workloads to reach required services. Shown: web and job SQL access, Prometheus scraping and trace export. Other manifests permit ingress, DNS, logs and secret-controller traffic.",
        424,
        p,
    )


def cloud():
    p = [heading("Cloud resources")]
    p += [d.card(284, 87, 692, 408), text(305, 118, "Google Cloud", 19, d.TEXT)]
    p += [d.card(306, 145, 334, 326, fill=d.BG), text(326, 177, "Dedicated VPC", 17, d.ACCENT)]
    p += [box(32, 210, 200, "Operator", "IAP + OS Login", d.ACCENT)]
    p += [box(326, 210, 294, "Compute Engine", "k3s host")]
    p += [box(326, 375, 294, "Retained disk", "Cluster state + volumes", d.OK)]
    p += [box(686, 210, 266, "Secret Manager", "Workload credentials")]
    p += [box(686, 375, 266, "GCS", "Terraform state")]
    p += [edge("M232,248 H317", d.ACCENT), edge("M473,286 V366")]
    p += [edge("M686,248 H629"), text(700, 195, "federation", 14)]
    return finish(
        "EPS cloud resources",
        "An authenticated IAP route reaches a k3s host in a dedicated VPC. A retained disk stores cluster data. Secret Manager supplies credentials through federation. GCS stores Terraform state separately.",
        520,
        p,
    )


def convergence():
    p = [heading("Configuration and idempotence")]
    rows = [
        (116, "Terraform", "Cloud resources", "Plan", "Compare resources with configuration"),
        (
            244,
            "Ansible",
            "Host + platform",
            "Check and apply",
            "Update changed files, charts and manifests",
        ),
        (
            372,
            "Argo CD",
            "Application resources",
            "Diff and sync",
            "Compare Git with the Kubernetes API",
        ),
    ]
    for y, title, sub, operation, detail in rows:
        p += [box(32, y, 245, title, sub), box(420, y, 548, operation, detail)]
        p += [edge(f"M277,{y + 38} H411"), text(348, y + 24, "desired state", 13, anchor="middle")]
    return finish(
        "Infrastructure configuration",
        "Terraform manages cloud resources, Ansible configures the host and platform, and Argo CD synchronizes application resources. Unchanged inputs avoid unnecessary changes and Helm upgrades.",
        480,
        p,
    )


def recovery():
    p = [heading("Database backup and restore")]
    for x, title, sub in [
        (32, "PostgreSQL", "Selected data + schema"),
        (365, "Off-VM archive", "Export + checksum"),
        (698, "Isolated restore", "Separate namespace + PVC"),
    ]:
        p += [box(x, 133, 270, title, sub)]
    p += [edge("M302,171 H356"), edge("M635,171 H689")]
    return finish(
        "Database recovery",
        "An operator exports PostgreSQL to an off-VM archive. Restoration into isolated storage checks schema, table counts and row checksums.",
        244,
        p,
    )


def roadmap():
    p = [heading("Next versions")]
    p += [box(32, 110, 410, "v0.6 · Managed services", "Cloud SQL + Cloud NAT", d.ACCENT)]
    p += [box(558, 110, 410, "v1.0 · Managed Kubernetes", "GKE + cloud ingress", d.ACCENT)]
    p += [edge("M442,148 H549")]
    return finish(
        "EPS roadmap",
        "v0.6 adds managed database and egress services. v1.0 moves workloads to GKE with cloud ingress and workload identities.",
        220,
        p,
    )


DIAGRAMS = {
    "kubernetes.svg": cluster,
    "observability.svg": observability,
    "telemetry-workflows.svg": telemetry_workflows,
    "alerts.svg": alerts,
    "delivery.svg": delivery,
    "secrets.svg": secrets,
    "security.svg": security,
    "cloud.svg": cloud,
    "idempotence.svg": convergence,
    "recovery.svg": recovery,
    "roadmap.svg": roadmap,
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, draw in DIAGRAMS.items():
        document = draw()
        ElementTree.fromstring(document)
        (OUT / name).write_text(document, encoding="utf-8", newline="\n")
        print(name)


if __name__ == "__main__":
    main()
