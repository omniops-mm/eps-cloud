# Infrastructure

Terraform defines the EPS host and its network access. Ansible configures Debian and installs the pinned k3s server. Application and platform manifests remain under `deploy/`; these directories do not contain duplicate Helm values.

This configuration is prepared for v0.5. It has not provisioned a cloud host or demonstrated a rebuild. The k3s security gate in `deploy/platform/host-versions.json` blocks boot-disk provisioning and Ansible configuration until the exact release is accepted.

## Terraform

The configuration uses the existing project and default network in Frankfurt. It manages the dedicated host identity, IAP SSH rule, a following ingress deny rule, a 40 GiB retained boot disk and one E2 standard-2 VM. The host identity receives no project roles. The VM uses OS Login, Shielded VM features and an ephemeral external address for downloads. Higher-priority and hierarchical firewall policies still require effective-access verification.

Create the GCS state bucket once outside this root configuration. Enable object versioning, uniform bucket-level access and public-access prevention. Grant state access only to the operator. Keep the bucket outside the host destruction lifecycle. The GCS backend provides state locking.

Initialize with the private bucket name and a stable prefix:

```text
terraform -chdir=infra/terraform init -backend-config="bucket=YOUR_PRIVATE_STATE_BUCKET" -backend-config="prefix=eps/host"
```

Use Application Default Credentials. Never place credentials in Terraform variables or backend configuration. Supply the project ID and exact dated Debian 13 image through an ignored variable file. Import the existing `eps-box` service account and `allow-iap-ssh` firewall into their declared resource addresses before the first cloud plan. Review every import and planned replacement against the actual project.

The VM defaults to `TERMINATED` and deletion protection is enabled. A stopped desired state does not guarantee that initial creation incurs no runtime charge. The disk remains billable and cannot be destroyed through this configuration. Set `desired_status` to `RUNNING` only for an attended session. Saved plans and state are private files.

Offline validation requires no cloud credentials:

```text
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```

The native test checks that an unaccepted k3s release prevents provisioning.

## Ansible

Use a Linux control environment with the packages in `ansible/requirements.txt`. Copy the example inventory into an ignored `inventory.local.yaml`, select the existing OS Login identity and establish an IAP tunnel to port 22 on loopback port 2222. Verify the SSH host key independently and retain strict host-key checking. No account, key or tunnel is created by the playbook.

```text
ansible-playbook -i inventory.local.yaml site.yaml --check
ansible-playbook -i inventory.local.yaml site.yaml
```

Run these commands from `infra/ansible`. The hardening role preserves OS Login and local forwarding for private access. The k3s role verifies release checksums, reuses the existing encryption and ingress configuration, and refuses an implicit version upgrade. It does not copy administrator kubeconfig off the host.

## Acceptance

Cloud acceptance still requires state-bucket access checks, imported-resource plan review, effective IAP and public-access tests, host configuration, platform installation and cloud workload federation. Platform installation is not yet automated by this playbook.

Run Ansible twice and review the second run for unintended changes. Rebuild on a separately named host and disk, restore an off-host backup, and repeat application, telemetry and access checks before retiring the original environment. Retaining the original disk alone does not demonstrate a clean rebuild.

References: [GCS backend](https://developer.hashicorp.com/terraform/language/backend/gcs), [Compute instance](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/compute_instance), [Ansible control environments](https://docs.ansible.com/projects/ansible/latest/installation_guide/intro_installation.html).

## Configuration review

The static scan identifies the planned external IPv4 address, Google-managed disk encryption and the explicit all-port ingress deny rule. The address is retained for the v0.4 outbound-download design; its effective firewall restrictions still require live verification. Customer-managed disk keys are not configured. The all-port rule denies traffic rather than allowing it. No scanner exclusions were added.
