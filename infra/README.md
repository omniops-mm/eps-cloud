# Infrastructure

Terraform defines the EPS lab host. Ansible hardens Debian and installs the pinned k3s release. Shared platform and application configuration remains under deploy/.

The configuration uses a dedicated Frankfurt VPC/subnet, an unprivileged host identity, IAP-only SSH and restricted egress. The Shielded VM has a retained 40 GiB disk, an ephemeral address for downloads and a stopped default. Ansible disables password/root login and agent forwarding, and installs a four-hour shutdown timer. The cloud host, effective firewall rules and IAP SSH were rechecked on 19 September 2026. Retained-disk reboot and isolated database restore passed; replacement-host recovery was not tested.

The [dated lab exception](../docs/lab-security.md) requires explicit Terraform lab_exception=true and Ansible eps_lab_exception=true. The normal release gate remains closed.

## Terraform

Create a private GCS state bucket separately with versioning, uniform access and public-access prevention. Grant state access only to the operator. Initialize the GCS backend with its bucket name and a stable prefix.

Use Application Default Credentials and an ignored variable file for the project and exact dated Debian 13 image. Import the existing eps-box service account before applying. Inspect existing resources first; the new EPS firewall rules belong to the dedicated network, so do not import the old default-network IAP rule. Review the plan before any apply.

Set desired_status=RUNNING only for attended testing. The default is TERMINATED; initial creation may still incur runtime charges. Retained disks and state remain billable. Saved plans and state are private.

Offline validation uses init -backend=false, fmt -check, validate and test from infra/terraform. The CI infrastructure job runs these checks without cloud credentials.

## Ansible

Run from Linux with infra/ansible/requirements.txt. Adapt the inventory example privately for IAP SSH and verify the host key. The boot script publishes only the public host key through the authenticated Compute serial-output API; interactive serial access remains disabled. Never copy local Google credentials or SSH keys onto the VM. Run syntax/lint checks before configuration; then verify a second run makes no unintended changes.

The complete host/k3s/platform playbook passed on 19 September 2026. Its final unchanged-input run reported 127 successful tasks, zero changes and zero failures. Helm revisions stayed unchanged. All workloads were Ready, secret encryption was enabled, and services used ClusterIP without NodePorts. Existing namespace security labels and the Git-synchronized application were preserved.

Workload federation accepts only the two named EPS secret identities and grants access per secret. Supply the same public issuer/JWKS variable file for every later plan, including shutdown. Terraform manages secret containers and IAM only; payloads are provisioned separately and never enter state. Cloud federation passed allowed/denied access checks.

An optional eps_image_bundle imports locally exported, checksum-verified platform archives without registry credentials. Set eps_platform_bundle to the private platform-bundle directory and eps_secret_store_manifest to an operator-rendered SecretStore file to install the pinned cert-manager, External Secrets, monitoring and Argo charts plus the reviewed policies. The platform role verifies every chart archive checksum and uses the imported images without copying registry credentials. Run the playbook twice and inspect the second run for zero unintended changes. An off-VM database archive was restored into isolated storage with matching schema, row counts, row checksums and a synthetic canary. The original database and volumes were preserved. Backups in this attended lab are operator-run, not scheduled.

From the Linux runtime, the attended lab invocation is `ansible-playbook -i /private/cloud-lab/access/inventory.yaml infra/ansible/site.yaml -e eps_lab_exception=true -e eps_image_bundle=/private/cloud-lab/platform-images -e eps_platform_bundle=/private/cloud-lab/platform-bundle -e eps_secret_store_manifest=/private/cloud-lab/secret-store.yaml`. Keep the inventory, chart/image bundles and rendered SecretStore private; do not pass registry or Google credentials to Ansible.
