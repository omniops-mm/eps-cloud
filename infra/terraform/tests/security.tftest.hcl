mock_provider "google" {}

variables {
  project_id = "eps-validation"
  boot_image = "projects/debian-cloud/global/images/debian-13-trixie-amd64-v20260901"
}

run "blocked_host_cannot_be_provisioned" {
  command         = plan
  expect_failures = [google_compute_disk.boot]

  assert {
    condition     = google_compute_firewall.iap.priority < google_compute_firewall.deny_ingress.priority && google_compute_firewall.iap.source_ranges == toset(["35.235.240.0/20"]) && one(google_compute_firewall.iap.allow).protocol == "tcp" && toset(one(google_compute_firewall.iap.allow).ports) == toset(["22"]) && one(google_compute_firewall.deny_ingress.deny).protocol == "all"
    error_message = "Only the IAP source range may precede the ingress deny rule."
  }

}

run "isolated_lab_requires_explicit_exception" {
  command = plan
  variables {
    lab_exception = true
  }
  assert {
    condition = !google_compute_network.eps.auto_create_subnetworks && google_compute_instance.eps.desired_status == "TERMINATED" && google_compute_instance.eps.metadata["enable-oslogin"] == "TRUE"
    error_message = "The lab must use a dedicated network, OS Login and a stopped default."
  }
  assert {
    condition = google_compute_firewall.deny_private_egress.priority < google_compute_firewall.web_egress.priority && google_compute_firewall.web_egress.priority < google_compute_firewall.deny_egress.priority && toset(one(google_compute_firewall.web_egress.allow).ports) == toset(["80", "443"]) && one(google_compute_firewall.deny_egress.deny).protocol == "all"
    error_message = "Private destinations must be denied before web egress; all remaining egress must be denied."
  }
}

run "host_key_bootstrap_uses_linux_line_endings" {
  command = plan
  variables {
    lab_exception = true
  }
  assert {
    condition = !strcontains(google_compute_instance.eps.metadata["startup-script"], "\r") && strcontains(google_compute_instance.eps.metadata["startup-script"], "/etc/ssh/ssh_host_ed25519_key.pub")
    error_message = "The bootstrap must use Linux line endings and publish only the public SSH host key."
  }
}
