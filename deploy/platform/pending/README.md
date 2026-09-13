# Pending Argo activation

Argo remains outside the installable platform registry. Its pinned vendor image has four fixable HIGH findings. A local PCRE2/SQLite package update passes filesystem and derived-inventory scans, but the candidate is not published or deployed.

The separate [k3s lab exception](../../../docs/lab-security.md) permits the private host; it does not approve the Argo image. KEDA was not selected. Request scaling uses the reviewed Prometheus Adapter and native HPA.

The prepared Argo configuration restricts management to the EPS namespace, disables anonymous access and unused controllers, and excludes Secret/RBAC API grants from its application roles. Creating workloads still allows consumption of existing Secrets. Redis requires a privately provisioned password Secret.

Image delivery, controller compatibility, effective permission checks, repository access, synchronization, failed-migration blocking and rollback remain unverified. The operator verifies and commits deployment snapshots; Argo never writes to GitHub.
