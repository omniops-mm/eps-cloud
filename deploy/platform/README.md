# Platform installation contract

ESO and production PostgreSQL use reviewed, digest-pinned Docker Hardened Images. Local registry access, database compatibility, federation and telemetry acceptance are recorded in [v0.4 readiness](../../docs/v0.4-readiness.md). Cloud deployment and live GitOps acceptance remain open. The procedures below define installation requirements; the readiness document records which checks have passed.

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
3. Grant `roles/secretmanager.secretAccessor` on individual secrets: the `eps` principal reads `eps-app`, `eps-db-admin`, `eps-exporter`; the `monitoring` principal reads only `eps-grafana`. Do not grant project-wide access or grant Secret Manager access to the VM identity.
4. Apply the identities, scoped controller releases and private stores, then the existing ExternalSecrets. Confirm readiness without printing secret values. Test cross-namespace token/Secret denial, an ordinary pod's denied Secret Manager access, and metadata denial.
5. Test webhook renewal, secret refresh and failures. Replacing a Kubernetes Secret does not rotate database passwords or restart applications automatically.

Uploaded JWKS needs an explicit rotation procedure: register new public keys before the cluster starts signing with them, retain old keys until outstanding tokens expire, then remove them. Recreating the cluster changes its trust material. Terraform in v0.5 should automate resources/IAM/public keys, not secret payloads. GKE migration uses GKE Workload Identity configuration; the application's Secret names/fields can remain the same.

Anyone authorized to create pods using a federated ServiceAccount can act as that identity. Restrict workload creation and SecretStore edits to trusted operators. Cluster/node administrators remain trusted; namespace separation is not a defense against host compromise.

The old `eso-metadata.yaml` exception has been removed. Keep `metadata-policy.yaml`; if migrating an existing installation, explicitly remove the old policy after switching authentication. No running installation is migrated by editing these files.

References: [ESO Google provider](https://external-secrets.io/main/provider/google-secrets-manager/), [Google federation for private Kubernetes](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-kubernetes).

## GitOps preparation

`argocd-project.yaml` permits only this repository, the `eps` namespace and the application chart's resource kinds. It does not permit Secrets, secret providers, RBAC or cluster resources. This is an Argo policy boundary, not a replacement for restricting the controller's Kubernetes permissions. Operators who can change these policies or create workloads remain trusted.

`argocd-application.yaml` watches the planned `production` branch's `chart/` directory. It deliberately has no automatic synchronization or cascading-deletion finalizer. No branch or release promotion is created by these files. Do not install it until a reviewed Argo image, restricted controller installation, private access, repository authentication and verified digest-based snapshot are ready. The prepared chart and controller configuration remain under `pending/` until their image and runtime gates pass.

The GitOps-only values layer reuses the existing migration Job: database wave 0, migration wave 1, web and scheduled jobs wave 2. Use a full application sync; selective resource synchronization skips hooks. A failed migration prevents the later wave from being applied, but existing pods and scheduled jobs may continue running. Migrations must remain compatible with the running version; this is not a maintenance-mode mechanism. HPA retains replica ownership. Production Helm installs outside Argo keep their existing init migration behavior.

Before enabling automation, prove failed-migration blocking, retained database storage, replica ownership and rollback in the isolated rehearsal. Rolling back application code does not undo database migrations.

References: [Argo projects](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/), [sync waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/), [sync options](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/).

## Release snapshot artifact

After all image signing/verification jobs succeed on a master push, CI creates `release-snapshot`: a chart copied from that exact Git commit, web/worker digests from the same run and attempt, release metadata, and a checked GitOps render. The chart supports digest references for web, its migration, and worker jobs; ordinary local examples retain commit tags. Kubernetes uses its ingress controller, so the separately published Compose nginx image is not inserted into this chart.

The snapshot job has read-only repository permissions and does not create or update `production`. `deployment_ready: false` records the remaining platform-image and live-validation gates; it is a status marker, not an access control. No cloud credentials or secret payloads are included by the generator. Chart source must still pass review and secret checks. Download artifacts only from the intended successful trusted run: running the generator with arbitrary JSON is not signature verification. Promotion must later recheck the source is still current, preserve branch protections, and publish the complete snapshot in one operator-authored commit. Automatic deployment remains disabled.

## Argo image verification

`Dockerfile.argocd` replaces two pinned Debian libraries in the digest-pinned donor. CI verifies the donor signature, checks every exported runtime path against the original and scans both the image and its complete derived inventory. The derived inventory preserves all donor components; it is not vendor-signed. Transparency-log verification is skipped explicitly. Package hashes and versions are recorded in `argocd-image.json`.

Argo builds run on master pushes and require the repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_READ_TOKEN`, using a temporary read-only Docker token. CI removes the registry login after verification, then publishes and signs the checked image in GHCR. After the required run succeeds, revoke the token in Docker and delete its GitHub secret. Subsequent Argo builds require a replacement token. Published images remain available; publication does not establish deployment readiness.

## Operator-controlled promotion

CI never commits or pushes repository changes. After the image and live acceptance gates pass, download the snapshot from one successful master run. Run `python -m scripts.verify_release <snapshot-directory>` from the source checkout. The verifier checks the GitHub run identity, downloads the image digest records directly from that run, reconstructs the expected chart from its exact source commit, compares every deployable file and rejects a superseded source.

The operator places only `chart/` and `release.json` into the production checkout, reviews the complete diff, and executes the commit and normal push. Do not include `rendered.yaml`, which is a derived preview. Re-run verification immediately before committing. If master advances or the production push is rejected, inspect the newer state rather than forcing an update.

GitHub branch protection remains in force. Verification does not authorize deployment or prove platform readiness, and it cannot make the source check and later operator push atomic. Argo remains manual until its acceptance checks pass.

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

The app chart defaults to `monitoring.enabled=false` in both development and production. Enable it only after installing a reviewed Prometheus Operator platform with matching CRDs, Prometheus and Alertmanager pods in the `monitoring` namespace, and Grafana with datasource UID `prometheus`. The label `release: monitoring` identifies the app ServiceMonitor and PrometheusRule. The pinned platform values, native dashboard provisioning and exporter policies below provide those prerequisites; the app chart does not install them.

Enabling monitoring creates a web ServiceMonitor, availability/error/CronJob alerts, three dashboard definitions, and the internal alert receiver. Configure Alertmanager to send resolved and firing notifications to `http://alert-log.eps.svc.cluster.local:9091/`, with `max_alerts: 100`. NetworkPolicies require both the monitoring namespace and the Prometheus or Alertmanager pod label. The web policy permits TCP port 8000, including all HTTP paths on that port; Kubernetes NetworkPolicy does not filter URL paths. The receiver uses the same image digest as web and mounts no credentials.

Run `python -m scripts.check_monitoring --rules-output <directory>` for strict custom-resource schemas, rendered pod/network/image checks, namespace substitution and ordinary Kubernetes schemas. The upstream Prometheus Operator v0.94.0 schema downloads are checksum-verified using `monitoring-schemas.json`; this pins validation data, not approval of a controller image. CI also uses promtool to parse the rendered alert and dashboard expressions. These checks do not prove scrape connectivity, populated panels or alert delivery. Monitor permissions must be reviewed again when selecting the platform chart.

## Monitoring platform configuration

`versions.json` pins kube-prometheus-stack 91.0.0 and prometheus-postgres-exporter 8.2.0 by archive checksum. Render the stack as release `monitoring` in namespace `monitoring`, and the exporter as `postgres-exporter` in namespace `eps`. The matching values files pin every runtime image by digest. Both namespaces need a separately provisioned `dhi-pull` Secret. The controller installation helper remains restricted to cert-manager and External Secrets.

Grafana uses the existing `monitoring/eps-grafana` administrator Secret and disables anonymous access and self-registration. All monitoring services are ClusterIP with no ingress. Access the UI through a loopback-only port-forward or private tunnel. Grafana uses native file provisioning, no sidecars, no download container and no Kubernetes API token or RBAC. `scripts.check_platform.dashboard_configmap()` creates the monitoring-namespace ConfigMap from the three existing app dashboard JSON files, substituting only the EPS namespace. Apply it before installing Grafana; `dashboardsConfigMaps` mounts it directly. The administrator Secret remains available to the Grafana process for authentication. If upgrading an older installation, explicitly remove its obsolete Grafana Roles and RoleBindings after verifying this configuration; deleting their source file does not remove live grants.

The exporter runs in `eps` as UID 65532, without a service-account token, using only `eps-exporter/DATA_SOURCE_NAME`. The connection string must use the `eps_exporter` database role and the `db` Service. The redundant monitoring-namespace ExternalSecret is removed from source. If an older deployment created one, removing its manifest does not delete the live Secret; inspect and retire that unused copy separately. `exporter-networkpolicies.yaml` allows its TCP connection to database port 5432 and permits scrapes on 9187 only from Prometheus pods in `monitoring`. DNS uses the existing app namespace policy. These rules require a CNI that enforces NetworkPolicy.

Prometheus retains three days of metrics with a 2GB retention target and 4Gi PVC. Grafana uses a 1Gi PVC. Explicit CPU/memory limits bound the main components. Kubelet provides container CPU/memory metrics; the host-mounted node exporter is disabled. The operator's webhook certificates use the existing cert-manager installation, eliminating the certificate patch Job. The exporter ServiceMonitor selects release `monitoring` and provides the existing PostgreSQL dashboard query.

**Image review, 13 September 2026:** the matched operator/reloader 0.94.0 upgrade passed filesystem and verified-inventory checks. The vulnerable dashboard sidecar is removed. Earlier Prometheus/Grafana inventory findings incorrectly matched scoped `@types/http-proxy` and `@types/js-cookie` components to unscoped runtime packages. Restoring the missing CycloneDX scope from each existing package URL in a derived scan input produced zero fixable HIGH/CRITICAL findings; the original signed inventories were retained unchanged. No package, version or finding was excluded. Signed inventories were verified against Docker's published DHI key with `--skip-tlog`; transparency-log verification is not claimed. Recheck image findings before later deployment.

Run `python -m scripts.rehearse_monitoring` with Helm on PATH for the dedicated `eps-v04-dev` cluster. It verifies chart checksums, retains existing credentials, provisions only the local DHI pull credential and a random local Grafana password when absent, mounts dashboards and installs monitoring/Loki/Tempo/Alloy. It never changes the default context or accesses Google. The initial operator webhook can reject rule creation while starting; if that specific bootstrap error occurs, wait for the operator to become ready and rerun. Do not disable webhook validation. Releases, credentials and PVCs remain locally. PostgreSQL exporter and app monitoring activation still require their separate database/application prerequisites.

`python scripts/check_platform.py` validates all pinned chart renders, ordinary Kubernetes schemas, instantiated CRD schemas, private services, pod/image restrictions, Grafana RBAC and exporter Secret/network boundaries. The check is read-only. The upstream chart warns that its boolean default for `arbitraryFSAccessThroughSMs` is replaced with a map; the rendered `{deny: true}` matches the Prometheus CRD and is checked explicitly. Live startup, scrape connectivity, alert delivery, dashboard data and private-access tests remain required.

References: [Docker image verification](https://docs.docker.com/dhi/how-to/verify/), [Prometheus Operator API](https://prometheus-operator.dev/docs/api-reference/api/).

## Log and trace backend configuration

`versions.json` pins Alloy 1.12.1 and Loki 7.3.0 from Grafana's chart repository, and Tempo 2.4.0 from its maintained community repository. Each release uses its own name in `monitoring`. Runtime images are digest-pinned DHI Alloy 1.19.2, Loki 3.7.7 and Tempo 2.10.8. On 13 September 2026, filesystem and verified CycloneDX inventory scans found no fixable HIGH/CRITICAL vulnerabilities in these three linux/amd64 candidates. Signature verification used Docker's key with `--skip-tlog`; it does not establish transparency-log verification. Recheck images before installation.

Alloy discovers only `app=web` or `app=jobs` pods in `eps` and keeps the current `web`, `refresh-weather` and `cleanup-audit-log` containers. Database, migration and controller containers are excluded. A namespace Role grants pod discovery and pod-log reads; Kubernetes RBAC grants these reads throughout `eps`, because label filtering is a collector setting rather than an authorization boundary. There is no Secret API access, cluster-wide role or host log mount. New job container names require an explicit collector update.

Logs retain only namespace, pod, container and app as indexed labels. Request/trace IDs remain in the original JSON body. The collector does not sanitize arbitrary message contents: application log privacy checks and a live private-marker test remain necessary. Alloy runs one replica with a 64Mi ephemeral data directory; this is not a durable delivery guarantee. The chart's config checksum triggers a rollout on Helm configuration changes, so a separate reloader image is disabled. Direct edits to the live ConfigMap do not trigger that rollout.

Loki uses one instance, filesystem storage on a 4Gi PVC, a 48-hour retention policy and bounded ingestion/query concurrency. Tempo uses one instance, a 2Gi PVC and 24-hour retention. These are small-environment limits, not high availability or backups. Retention is asynchronous and does not prevent a busy workload from filling a volume before old data expires. PVCs survive workload removal. Both storage pods run as non-root with read-only root filesystems and no Kubernetes API token.

Apply `telemetry-network.yaml` before installing the backends. Loki port 3100 accepts only Alloy and Grafana pods in `monitoring`. Tempo accepts OTLP HTTP on 4318 only from `app=web` pods in `eps`, and queries on 3200 only from Grafana. Alloy has no Service and denies pod ingress. These restrictions depend on CNI enforcement; Loki and Tempo do not provide application authentication here. Permitted Loki clients can access all HTTP routes on 3100, not just a write or query path. Cluster/node administrators and operators who can modify workload labels or policies remain trusted. The existing monitoring egress policy blocks link-local metadata but does not restrict all outbound destinations.

Tempo's chart requires Jaeger-shaped values for its port templates, so its actual receiver configuration is explicit: only OTLP HTTP is enabled. Legacy Service/container port declarations from the chart do not start receivers and are not allowed by the ingress policy. Grafana provisions internal Loki and Tempo data sources with log-to-trace and trace-to-log links. These links require application trace IDs; their opt-in application configuration is described below. No production tracing or monitoring switch is enabled by these files.

The image gate is cleared for local installation; live integration checks remain open. Native configuration parsing passed with all three exact pinned images in network-disabled, read-only disposable containers using stdin and no host mounts. Before deployment, provision `monitoring/dhi-pull` privately, complete isolated startup checks, verify allowed/denied client traffic, generate fresh log/trace data, test privacy markers and restart persistence. No cloud resource or platform release is created by this configuration batch.

References: [Alloy configuration validation](https://grafana.com/docs/alloy/latest/reference/cli/validate/), [Loki retention](https://grafana.com/docs/loki/latest/operations/storage/retention/), [Tempo community charts](https://github.com/grafana-community/helm-charts/tree/main/charts/tempo).

## Optional application tracing

Tracing defaults to disabled. After Tempo startup, storage and network checks pass, enable the app chart's `tracing.enabled` setting; `tracing.sampleRate` defaults to 0.1 and the application validates it as a finite value between 0 and 1. The chart sets only the web container's tracing configuration and adds TCP 4318 egress to Tempo pods in `monitoring`. The export destination is fixed to the internal Tempo OTLP HTTP endpoint. This opt-in layer does not install Tempo or clear its live verification gates.

The SDK creates a fresh root for each non-probe web request, with child database and weather-fetch spans. Inbound trace headers cannot choose sampling or introduce baggage/tracestate, and weather requests do not receive trace headers. Root decisions are inherited by child spans. This intentionally does not continue traces from an upstream service. Background worker processes are not instrumented. Export reconstructs spans with fixed service/scope metadata, known route patterns, status codes and bounded database operation names. It discards arbitrary attributes, SQL/URL text, exception events, status descriptions, links and tracestate. Existing JSON request logs receive the trace/span IDs; sampled requests attach exemplars to counters and latency histograms. Unsampled IDs have no stored trace. Metrics negotiate OpenMetrics for exemplar-capable scrapers and retain plain Prometheus output for existing clients.

Export runs on the SDK's background worker with a 256-span queue, batches of 32, bounded span attributes and a two-second transport timeout. The transport disables environment proxy/netrc settings and redirects. Failed exports do not fail web requests; full queues or export failures can lose traces. Library errors use the existing sanitized logging pipeline. The application never waits for a flush during a request. Tests cover serialized-payload privacy, parent/child relationships, request isolation, sampling, CSRF/errors, correlation and a blocked exporter. Cluster ingestion, actual Grafana links and network enforcement still require the live rehearsal.

## Local application telemetry rehearsal

After the monitoring platform is ready, download the `image-digest-web` and `image-digest-worker` artifacts from one successful master CI run into a directory outside the repository. Run `python -m scripts.rehearse_observability <record-directory>` with Helm and GitHub CLI on PATH. The helper checks the run identity and both digest records, uses the dedicated loopback-only local context, and refuses to replace existing application credentials. An unused namespace receives random local credentials; subsequent runs retain them. It renders the existing production chart with one web replica, 1Gi database storage, monitoring and full test trace sampling. It omits Ingress and suspends both CronJobs before applying resources. No production values or Google secrets are changed. Local credentials, workloads and PVCs remain until explicitly retired.

`python -m scripts.check_live_observability` sends one local request with a random test marker, checks web/database scrapes and OpenMetrics exemplars, and follows the request log from Loki to its actual Tempo trace. It checks that the query/header marker is absent from those stored payloads. It then creates a temporary unprivileged pod to test denied network paths against the verified healthy services, and a temporary warning-level Prometheus rule to verify firing and resolved delivery through Alertmanager. Informational alerts are intentionally inhibited by the upstream chart, so they are not suitable for this delivery check. Probe pods, rules and loopback tunnels are removed on completion or failure. These checks do not prove backup recovery, storage durability or every possible sensitive-data path.

## Private ingress and native request scaling

`private-issuer.yaml` supplies the issuer already referenced by the app ingress. A private CA key stays in cert-manager; the blackbox exporter mounts only its public trust anchor from `monitoring/eps-ingress-ca`. `python -m scripts.rehearse_ingress` requires loopback-bound k3d ingress ports, installs the local certificate/probe and restricts the probe endpoint to Prometheus. The probe validates the CA and intended TLS name and refuses redirects. CA rotation requires a deliberate trust-bundle overlap and leaf-certificate renewal; do not treat the copied ConfigMap as automatic CA rotation. The probe covers Traefik and application/database readiness, not IAP authentication.

The reviewed Prometheus Adapter maps EPS pod counters to `eps_http_requests_per_second` through the authenticated, cert-manager-backed custom metrics API. The existing native HPA combines CPU and per-pod request rate. `hpa.requestsPerSecond: 0` retains CPU-only scaling; a positive value requires monitoring. Missing backend metrics remain errors, not synthetic zeroes. Production downscale stabilization defaults to 300 seconds. No KEDA controller or second HPA is installed.

For the local bounded drill, run `python -m scripts.rehearse_observability <record-directory> --request-scaling`, then `python -m scripts.check_live_observability --scaling`. It uses 1-3 replicas, a 0.1 request/second target, 30-second downscale stabilization and a deliberately unreachable CPU target to isolate the request signal. The checker stops after bounded read-only traffic, briefly stops/restores the adapter, verifies an API error during the outage and waits for downscale. These settings are test-only; production defaults are not enabled by the helper. Recovery/key/catch-up commands are documented in `deploy/ops/README.md`.

Argo settings under `pending/` are excluded from the installable chart registry. Their image and runtime acceptance gates remain closed. See `docs/v0.4-readiness.md` for the exact release boundary.
