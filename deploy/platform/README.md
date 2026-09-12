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
