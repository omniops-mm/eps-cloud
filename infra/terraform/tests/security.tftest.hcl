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
