# Private lab security exception

Approved 13 September 2026 for attended EPS learning sessions. The exception expires at 00:00 UTC on 20 September 2026 and requires reassessment before extension. It permits only the version and binary checksum in deploy/platform/host-versions.json. The general approved flag remains false. Terraform requires lab_exception=true; Ansible requires eps_lab_exception=true. Neither setting declares the release vulnerability-free.

## Evidence and residual risk

The reviewed k3s 1.36.4+k3s1 image contains 26 fixable HIGH findings across k3s, runc and the containerd shim. The checksum-verified VM distribution contains the same runtime binaries. Binary analysis found affected symbols; exploit reachability has not been established. The accepted scope covers these recorded occurrences, not future findings or other images.

| Runtime | Affected modules | Occurrences |
|---|---|---|
| k3s | x/crypto, x/mod, x/net, gRPC | 16 |
| containerd shim | x/net, x/text, gRPC | 5 |
| runc | x/net | 5 |

Reviewed image: rancher/k3s@sha256:edad48e12bf81c3a09ac1c05c0c0ffaaa22145980b989d6fae84543a76b83657. Existing scan reports remain unchanged. No scanner ignore rule is introduced. Reassess the exact binaries and findings before deployment, after any release change and at expiry.

## Required isolation

- Use synthetic application data and dedicated lab secrets. Personal data, production credentials, Google login files, repository write tokens and SSH private keys must not enter the VM.
- Use a dedicated IPv4 VPC and subnet, with no peering, VPN or route to the operator network. Allow inbound TCP 22 only from IAP, authenticated through operator IAM and OS Login. Deny other ingress, including HTTP, HTTPS and the Kubernetes API.
- Deny outbound private-network traffic. Permit public TCP 80/443 for signed package downloads, registries and Google APIs; deny other outbound traffic. Web egress still permits exfiltration. Google metadata traffic is not blocked by VPC firewall rules.
- Give the VM identity no project roles. Scope workload federation to required lab resources and verify effective IAM before provisioning.
- Retain restricted pod permissions, network policies and encrypted Kubernetes secrets. Root or cluster compromise can still expose every secret available to that cluster.
- Use strict SSH host-key checks, no agent forwarding, no reverse tunnels and no shared PC filesystem. Bind temporary application tunnels to loopback and verify TLS. Do not install the lab CA into the system trust store.
- Stop the VM after testing. A four-hour systemd timer is a backstop, not a security boundary: root can disable it. Verify the cloud reports TERMINATED and close local tunnels. Retained disk and state storage remain billable.

Expiry blocks new Terraform plans and Ansible configuration; it does not revoke a running VM. No automatic exception renewal is provided. CI's exception acceptance test intentionally fails after expiry.

## Acceptance

Configuration checks do not prove cloud isolation. Before application deployment, verify effective firewall policies, operator-only IAP/OS Login permissions, host identity grants, direct-access denial and authenticated tunnel access. Then verify workload denial paths, sensitive-data handling, recovery and shutdown. The app remains a private lab; no public endpoint or public release claim is authorized by this exception.

References: [IAP TCP forwarding](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding), [VPC firewall semantics](https://docs.cloud.google.com/firewall/docs/firewalls).
