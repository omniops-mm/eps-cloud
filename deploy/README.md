# Deployment layout

The Helm chart defines the application. Platform configuration defines the controllers and telemetry services that support it. Infrastructure automation defines the host on which they run.

| Path | Responsibility |
|---|---|
| `helm/eps/` | Web, database, scheduled jobs, migrations, application policies and autoscaling. |
| `platform/` | Pinned platform charts, shared policies, secrets integration and observability. |
| `platform/argocd-*.yaml` | Application project and source selection for GitOps. |
| `platform/pending/` | Controller configuration excluded from installation until its acceptance gates pass. |
| `ops/` | Recovery and credential-maintenance procedures. |
| `raw-manifests/` | Generated baseline manifests for the original local deployment. Update them through `render_raw.py`. |
| `../infra/` | Terraform host resources and Ansible host configuration. |
| `../scripts/rehearse_*.py` | Explicitly scoped local installation and acceptance tools. |

## Release ownership

CI validates source, builds and scans images, verifies signatures and publishes a digest-pinned chart snapshot. It has no repository-write job. The operator verifies that snapshot, reviews its changes and executes the production commit and push.

Argo reads `production:chart/`. It manages application resources only; it does not install the platform or create Git commits. Initial synchronization is manual. The `production` branch contains the verified chart and release metadata, rather than another copy of the development tree.

## Configuration layers

`values.yaml` provides the local baseline. `values-production.yaml` supplies production settings, and a verified release snapshot adds immutable application image digests. `values-gitops.yaml` adds Argo migration ordering. Platform features require their controllers and acceptance checks before activation.

See [platform installation](platform/README.md), [recovery](ops/README.md), and [current acceptance](../docs/v0.4-readiness.md).
