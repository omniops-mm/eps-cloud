# Platform installation contract

Installation remains blocked until the ESO image passes the vulnerability gate. Static checks do not prove cloud IAM, network enforcement, token exchange or certificate rotation.

## ESO releases

Install cert-manager first. Both ESO releases run in the `external-secrets` namespace, using the chart version in `versions.json` and the common `external-secrets-values.yaml` plus the matching override:

| Release | Override | Watches and accesses Secrets in | Owns shared CRDs/webhook |
|---|---|---|---|
| `external-secrets-eps` | `external-secrets-eps-values.yaml` | `eps` | Yes |
| `external-secrets-monitoring` | `external-secrets-monitoring-values.yaml` | `monitoring` | No |

The common file alone starts no ESO controller or webhook. Apply `eso-webhook-issuer.yaml` before the first release. cert-manager issues/renews its webhook certificate and injects its CA; ESO's separate certificate controller is disabled. cert-manager remains a trusted cluster-wide controller. The first ESO release owns the shared CRDs and webhook, so do not remove it independently while the second release is in use.

`eso-identities.yaml` creates an unmounted `eps-secrets` ServiceAccount in each target namespace. Each ESO controller can request a token only for that named identity in its own target namespace. The chart does not grant general token creation. Kubernetes Secret access is namespace-wide within each target, not restricted to individual Secret names.

## Google authentication

Use the supported `auth.workloadIdentityFederation.serviceAccountRef` mechanism in both documents of `secret-store.yaml.example`. Replace placeholders in a private copy outside Git. No JSON key, credential file, GCP service-account impersonation or VM metadata credentials are configured.

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
