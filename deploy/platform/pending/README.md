# Blocked controller activation

These chart pins are separate from the installable platform registry. Do not install Argo or enable GitOps promotion while its image gate remains unresolved. Request scaling uses the separately reviewed Prometheus Adapter.

13 September 2026: maintained Argo CD 3.5.2 has 4 fixable HIGH findings in PCRE2/SQLite; KEDA metrics server 2.20.2 has 2 in its Prometheus Go dependency. Current k3s 1.36.4+k3s1 also has 26 fixable findings and blocks cloud deployment. Original signed inventories and scan outputs are retained outside the repository. No finding exemptions or custom image forks were introduced.

KEDA was not selected: the maintained Prometheus Adapter passed review and uses the native HPA. Request-rate scaling remains off in production defaults until its deployment acceptance checks pass. No KEDA controller or ScaledObject is required.

Argo AppProject, ordered migration hooks and CI promotion already exist. Argo controller installation, Kubernetes permissions, repository authentication and failed-migration/rollback evidence remain blocked. Promotion stays disabled; do not create production snapshots manually to bypass the verified CI path.

The pending Argo values disable generated cluster roles, unused controllers and anonymous access. The cluster registration restricts managed namespaces to EPS and leaves credential fields empty; Argo 3.5.2 uses native in-cluster projected credentials for this internal endpoint. Companion EPS Roles allow controller changes and server reads only for the listed application resources, without Secret/RBAC API grants. Workload creation remains trusted because pods can consume existing Secrets. Redis initialization is disabled: provision its expected password Secret privately before installation. Runtime DHI compatibility, effective API denials, private repository access if needed, health/sync and rollback remain required. No static Kubernetes token or admin password is stored here.
