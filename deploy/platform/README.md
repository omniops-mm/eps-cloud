# Platform installation contract

Reviewed Docker Hardened Image candidates are now pinned for ESO and production PostgreSQL. Their 13 September 2026 image scans and signed-inventory checks passed; private registry access and live compatibility remain installation gates. Static checks do not prove cloud IAM, network enforcement, token exchange or certificate rotation.

## ESO releases

Install cert-manager first. Both ESO releases run in the `external-secrets` namespace, using the chart version in `versions.json` and the common `external-secrets-values.yaml` plus the matching override:

| Release | Override | Watches and accesses Secrets in | Owns shared CRDs/webhook |
|---|---|---|---|
| `external-secrets-eps` | `external-secrets-eps-values.yaml` | `eps` | Yes |
| `external-secrets-monitoring` | `external-secrets-monitoring-values.yaml` | `monitoring` | No |

The common file alone starts no ESO controller or webhook. Apply `eso-webhook-issuer.yaml` before the first release. cert-manager issues/renews its webhook certificate and injects its CA; ESO's separate certificate controller is disabled. cert-manager remains a trusted cluster-wide controller. The first ESO release owns the shared CRDs and webhook, so do not remove it independently while the second release is in use.

`eso-identities.yaml` creates an unmounted `eps-secrets` ServiceAccount in each target namespace. Each ESO controller can request a token only for that named identity in its own target namespace. The chart does not grant general token creation. Kubernetes Secret access is namespace-wide within each target, not restricted to individual Secret names.

## Google authentication

Use the supported `auth.workloadIdentityFederation.serviceAccountRef` mechanism in both documents of `secret-store.yaml.example`. Replace placeholders in a private copy outside Git. Set both the STS audience and the ServiceAccount token audience to the same provider: the former uses //iam.googleapis.com/... and the latter uses https://iam.googleapis.com/.... Explicit token audiences prevent the Kubernetes API audience from being used for Google authentication. No JSON key, credential file, GCP service-account impersonation or VM metadata credentials are configured.

Cloud setup is a separate operator task:

1. Confirm the intended k3s cluster's issuer and public JWKS. Configure a dedicated Google workload identity pool/provider for that cluster with the uploaded public JWKS; the Kubernetes API stays private. Never export signing private keys or Kubernetes tokens.
2. Map `google.subject=assertion.sub`. Restrict the provider condition to exactly `system:serviceaccount:eps:eps-secrets` and `system:serviceaccount:monitoring:eps-secrets`. Keep local/test clusters in separate trust configurations.
3. Grant `roles/secretmanager.secretAccessor` on individual secrets: the `eps` principal reads `eps-app`, `eps-db-admin`, `eps-exporter`; the `monitoring` principal reads `eps-grafana`, `eps-exporter`. Do not grant project-wide access or grant Secret Manager access to the VM identity.
4. Apply the identities, scoped controller releases and private stores, then the existing ExternalSecrets. Confirm readiness without printing secret values. Test cross-namespace token/Secret denial, an ordinary pod's denied Secret Manager access, and metadata denial.
5. Test webhook renewal, secret refresh and failures. Replacing a Kubernetes Secret does not rotate database passwords or restart applications automatically.

Uploaded JWKS needs an explicit rotation procedure: register new public keys before the cluster starts signing with them, retain old keys until outstanding tokens expire, then remove them. Recreating the cluster changes its trust material. Terraform in v0.5 should automate resources/IAM/public keys, not secret payloads. GKE migration uses GKE Workload Identity configuration; the application's Secret names/fields can remain the same.

Anyone authorized to create pods using a federated ServiceAccount can act as that identity. Restrict workload creation and SecretStore edits to trusted operators. Cluster/node administrators remain trusted; namespace separation is not a defense against host compromise.

The old `eso-metadata.yaml` exception has been removed. Keep `metadata-policy.yaml`; if migrating an existing installation, explicitly remove the old policy after switching authentication. No running installation is migrated by editing these files.

References: [ESO Google provider](https://external-secrets.io/main/provider/google-secrets-manager/), [Google federation for private Kubernetes](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-kubernetes).

## GitOps preparation

`argocd-project.yaml` permits only this repository, the `eps` namespace and the application chart's resource kinds. It does not permit Secrets, secret providers, RBAC or cluster resources. This is an Argo policy boundary, not a replacement for restricting the controller's Kubernetes permissions. Operators who can change these policies or create workloads remain trusted.

`argocd-application.yaml` watches the planned `production` branch's `chart/` directory. It deliberately has no automatic synchronization or cascading-deletion finalizer. No branch or release promotion is created by these files. Do not install it until a reviewed Argo image, restricted controller installation, private access, repository authentication and verified digest-based snapshot are ready. The Argo chart/install values will be pinned with that reviewed image.

The GitOps-only values layer reuses the existing migration Job: database wave 0, migration wave 1, web and scheduled jobs wave 2. Use a full application sync; selective resource synchronization skips hooks. A failed migration prevents the later wave from being applied, but existing pods and scheduled jobs may continue running. Migrations must remain compatible with the running version; this is not a maintenance-mode mechanism. HPA retains replica ownership. Production Helm installs outside Argo keep their existing init migration behavior.

Before enabling automation, prove failed-migration blocking, retained database storage, replica ownership and rollback in the isolated rehearsal. Rolling back application code does not undo database migrations.

References: [Argo projects](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/), [sync waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/), [sync options](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/).

## Release snapshot artifact

After all image signing/verification jobs succeed on a master push, CI creates `release-snapshot`: a chart copied from that exact Git commit, web/worker digests from the same run and attempt, release metadata, and a checked GitOps render. The chart supports digest references for web, its migration, and worker jobs; ordinary local examples retain commit tags. Kubernetes uses its ingress controller, so the separately published Compose nginx image is not inserted into this chart.

The snapshot job has read-only repository permissions and does not create or update `production`. `deployment_ready: false` records the remaining platform-image and live-validation gates; it is a status marker, not an access control. No cloud credentials or secret payloads are included by the generator. Chart source must still pass review and secret checks. Download artifacts only from the intended successful trusted run: running the generator with arbitrary JSON is not signature verification. Promotion must later recheck the source is still current, preserve branch protections, and publish the complete snapshot atomically. Automatic deployment remains disabled.

## Promotion activation gate

The `promote` CI job is disabled unless the repository variable `EPS_PROMOTION_ENABLED` equals `true`. It also uses the `eps-production` GitHub environment. Keep the variable absent until platform images, cloud authentication, the live rehearsal and protected-branch access are approved. Configure environment protection and master-only deployment rules before activation; an environment name alone does not provide approval protection. See [GitHub environment controls](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

Once enabled, successful master runs can update `production` automatically. This changes deployment input; Argo synchronization is still separately controlled. The job reconstructs the expected snapshot from the source commit and same-run image records, rejects extra/modified files, and publishes only `chart/` and `release.json`. Generated render output is checked evidence, not branch content. It checks current master before and immediately after constructing the promotion commit. Normal fast-forward push rejects concurrent production updates; there is no force or overwrite retry. Master can still advance in the brief interval between its final check and the push: this is not an atomic transaction across two branches. Newer runs reconcile later. Keep Argo manual while validating this behavior.

The snapshot's `deployment_ready: false` remains historical preparation status and is not used to authorize promotion. Activation is an explicit operator decision after the live gates. Do not change that field to bypass a failed check. Branch protections are enforced by GitHub; if they reject the Actions token, resolve the protection workflow rather than granting bypass rights. No production branch or GitHub environment is created during local preparation.

## Hardened image bootstrap and registry access

The ESO controller/webhook and production PostgreSQL use reviewed digest references. `dhi-pull` is a pre-existing registry Secret required in both `external-secrets` and `eps`; chart values contain only its name. Create it through the operator workflow using a read-only Docker token supplied privately. Do not copy the entire Docker config, put tokens in command arguments, or ask ESO to fetch its own initial pull credential. Initial registry access is a platform prerequisite, separate from Google Secret Manager authentication. Expiry/rotation of the pull token must be handled before recreating pods. Development defaults remain unchanged and need no DHI login.

With `db.bootstrapRoles: true`, a PostgreSQL init container now initializes fresh storage with native database tools, starts a temporary Unix-socket-only server, creates the existing database/roles, stops it and writes a completion marker. Role changes use a transaction. The ordinary server starts only after success. Application/exporter passwords are provided to the init container only; the runtime retains its existing administrator environment for the vendor entrypoint. This replaces dependence on `/docker-entrypoint-initdb.d`, which Docker's hardened entrypoint does not process.

The bootstrap checks PostgreSQL major 16 and refuses any existing database without its completion marker. A failure does not delete data or retry role creation against a partial database. Never add the marker manually to bypass a failed initialization. Investigate or restore from backup; only disposable rehearsal volumes may be explicitly recreated. This change is not an in-place migration procedure for older EPS volumes. Secret changes do not rotate existing PostgreSQL role passwords automatically.

Before production: use fresh isolated rehearsal storage, verify version/roles/database access and restart behavior with the exact images, and confirm image pulls work after a pod restart. Local shell tests use simulated PostgreSQL commands and prove control flow only. The Kubernetes database rehearsal below verifies initialization and restart behavior against the pinned image.

## Local Kubernetes registry rehearsal

Run `scripts/rehearse_registry.py` with Python on the Windows workstation after committing and passing CI. It uses the existing k3d/kubectl tools and creates a temporary kubeconfig for `eps-v04-dev`, without changing the default context. It refuses endpoints other than HTTPS 127.0.0.1:6550 and refuses disabled TLS verification.

First log into dhi.io using a personal access token with Read permission. The tool refuses stored account passwords; it checks the token format but cannot establish its permission scope. It reads only the dhi.io credential from Docker's configured helper or auth entry and sends a registry Secret through kubectl stdin. No credential is printed, placed in command arguments or written into the repo. The two named Kubernetes Secrets are deliberately retained for later installation. Do not run this tool against production.

The tool creates/labels only eps and external-secrets namespaces, applies dhi-pull without forcing ownership conflicts, and runs short non-root probes of the pinned PostgreSQL and ESO images. Probes mount no service-account token and expose no service/port. Their pods are removed afterward. This verifies authenticated cluster pulls and binary startup only, not ESO federation or database PVC behavior. A read-only token still requires expiration/rotation management. Re-running the tool updates the two managed Secrets using the current dhi.io login.

## Kubernetes database/PVC rehearsal

After the registry rehearsal passes, run `python -m scripts.rehearse_kubernetes_db` from the EPS repository. It reuses the same validated local context, copies only dhi.io access into a unique `eps-db-check-*` namespace, generates private temporary database credentials, and installs only the actual chart's db Service/StatefulSet and db-init ConfigMap. A default-deny NetworkPolicy isolates the test. No application, ingress, external-secret controller or cloud resource is installed.

The rehearsal waits for real init-container/PVC readiness, verifies database role privileges and authenticated access, writes a canary row, replaces the pod and checks data persistence. It deletes only its newly created namespace afterward, including its credentials and PVC. Existing eps resources and volumes are untouched. Cleanup failures are reported rather than bypassing finalizers. This proves the local chart/storage path; it does not prove cloud networking, backup recovery or Google federation.

## Local secret-controller installation

After the registry and Kubernetes database rehearsals pass, run Python module scripts.rehearse_secret_controllers from the repository. It verifies both chart archive hashes before mutations, uses only the validated local test context, installs digest-pinned cert-manager and both scoped ESO releases, and checks readiness plus allowed/denied Secret and named-token permissions using SubjectAccessReview. It applies the existing link-local egress policy to the controller and monitoring namespaces. These authorization checks do not prove network enforcement.

Controllers and their namespaces remain installed for the next step. A failed run leaves resources available for diagnosis; it never deletes CRDs or rolls back shared resources automatically. No Google trust, SecretStore, ExternalSecret or cloud resource is created. The registry credential must already exist. Certificate renewal and actual Google token exchange require separate integration checks.

## Application monitoring

The app chart defaults to `monitoring.enabled=false` in both development and production. Enable it only after installing a reviewed Prometheus Operator platform with matching CRDs, Prometheus and Alertmanager pods in the `monitoring` namespace, and Grafana with datasource UID `prometheus`. The label `release: monitoring` identifies the app ServiceMonitor and PrometheusRule. The pinned platform values, dashboard roles and exporter policies below provide those prerequisites; the app chart does not install them.

Enabling monitoring creates a web ServiceMonitor, availability/error/CronJob alerts, three dashboard definitions, and the internal alert receiver. Configure Alertmanager to send resolved and firing notifications to `http://alert-log.eps.svc.cluster.local:9091/`, with `max_alerts: 100`. NetworkPolicies require both the monitoring namespace and the Prometheus or Alertmanager pod label. The web policy permits TCP port 8000, including all HTTP paths on that port; Kubernetes NetworkPolicy does not filter URL paths. The receiver uses the same image digest as web and mounts no credentials.

Run `python -m scripts.check_monitoring --rules-output <directory>` for strict custom-resource schemas, rendered pod/network/image checks, namespace substitution and ordinary Kubernetes schemas. The upstream Prometheus Operator v0.93.1 schema downloads are checksum-verified using `monitoring-schemas.json`; this pins validation data, not approval of a controller image. CI also uses promtool to parse the rendered alert and dashboard expressions. These checks do not prove scrape connectivity, populated panels or alert delivery. Monitor permissions must be reviewed again when selecting the platform chart.

## Monitoring platform configuration

`versions.json` pins kube-prometheus-stack 90.2.0 and prometheus-postgres-exporter 8.2.0 by archive checksum. Render the stack as release `monitoring` in namespace `monitoring`, and the exporter as `postgres-exporter` in namespace `eps`. The matching values files pin every runtime image by digest. Both namespaces need a separately provisioned `dhi-pull` Secret. The controller installation helper remains restricted to cert-manager and External Secrets.

Grafana uses the existing `monitoring/eps-grafana` administrator Secret and disables anonymous access and self-registration. All monitoring services are ClusterIP with no ingress. Access the UI through a loopback-only port-forward or private tunnel. Its chart-generated RBAC is disabled because it grants Secret access even with ConfigMap-only sidecars. `grafana-dashboard-rbac.yaml` instead grants get/list/watch for ConfigMaps in `eps` and `monitoring`; this covers all ConfigMaps in those namespaces, not only labelled dashboards. It grants no Secret API access. The mounted administrator Secret remains available to the Grafana process as required for authentication.

The exporter runs in `eps` as UID 65532, without a service-account token, using only `eps-exporter/DATA_SOURCE_NAME`. The connection string must use the `eps_exporter` database role and the `db` Service. The redundant monitoring-namespace ExternalSecret is removed from source. If an older deployment created one, removing its manifest does not delete the live Secret; inspect and retire that unused copy separately. `exporter-networkpolicies.yaml` allows its TCP connection to database port 5432 and permits scrapes on 9187 only from Prometheus pods in `monitoring`. DNS uses the existing app namespace policy. These rules require a CNI that enforces NetworkPolicy.

Prometheus retains three days of metrics with a 2GB retention target and 4Gi PVC. Grafana uses a 1Gi PVC. Explicit CPU/memory limits bound the main components. Kubelet provides container CPU/memory metrics; the host-mounted node exporter is disabled. The operator's webhook certificates use the existing cert-manager installation, eliminating the certificate patch Job. The exporter ServiceMonitor selects release `monitoring` and provides the existing PostgreSQL dashboard query.

**Installation gate, 13 September 2026:** four of eight pinned image candidates passed filesystem and verified-inventory checks; Prometheus, Grafana, the dashboard sidecar and the version-matched config reloader still have fixable HIGH findings. Do not install this stack or enable app monitoring until those findings are resolved and the exact replacement digests are checked. Signed inventories were verified against Docker's published DHI key using its documented `--skip-tlog` mode; transparency-log verification is not claimed. Configuration/schema validation does not clear an image for deployment. No blanket vulnerability exclusions are used.

`python scripts/check_platform.py` validates all pinned chart renders, ordinary Kubernetes schemas, instantiated CRD schemas, private services, pod/image restrictions, Grafana RBAC and exporter Secret/network boundaries. The check is read-only. The upstream chart warns that its boolean default for `arbitraryFSAccessThroughSMs` is replaced with a map; the rendered `{deny: true}` matches the Prometheus CRD and is checked explicitly. Live startup, scrape connectivity, alert delivery, dashboard data and private-access tests remain required.

References: [Docker image verification](https://docs.docker.com/dhi/how-to/verify/), [Prometheus Operator API](https://prometheus-operator.dev/docs/api-reference/api/).
