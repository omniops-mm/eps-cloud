terraform {
  required_version = ">= 1.16.2, < 2.0"
  backend "gcs" {}
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.2.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = "europe-west3"
  zone    = "europe-west3-a"
}

locals {
  host = jsondecode(file("${path.module}/../../deploy/platform/host-versions.json"))
}

resource "google_compute_network" "eps" {
  name                    = "eps-lab"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "eps" {
  name                     = "eps-lab-frankfurt"
  network                  = google_compute_network.eps.id
  ip_cidr_range            = "10.90.0.0/28"
  region                   = "europe-west3"
  private_ip_google_access = true
}

resource "google_service_account" "eps" {
  account_id   = "eps-box"
  display_name = "EPS host"
  # Workload federation grants application access; the host receives no project roles.
}

resource "google_compute_firewall" "iap" {
  name                    = "eps-lab-iap-ssh"
  network                 = google_compute_network.eps.self_link
  priority                = 900
  direction               = "INGRESS"
  source_ranges           = ["35.235.240.0/20"]
  target_service_accounts = [google_service_account.eps.email]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "deny_ingress" {
  name                    = "eps-lab-deny-ingress"
  network                 = google_compute_network.eps.self_link
  priority                = 901
  direction               = "INGRESS"
  source_ranges           = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.eps.email]
  deny {
    protocol = "all"
  }
}

resource "google_compute_disk" "boot" {
  name  = "${var.instance_name}-boot"
  zone  = "europe-west3-a"
  type  = "pd-standard"
  size  = 40
  image = var.boot_image
  labels = {
    application = "eps"
  }
  lifecycle {
    prevent_destroy = true
    precondition {
      condition = local.host.k3s.approved || (
        var.lab_exception &&
        local.host.k3s.version == local.host.k3s.lab_exception.version &&
        local.host.k3s.binary_sha256 == local.host.k3s.lab_exception.binary_sha256 &&
        timecmp(plantimestamp(), "${local.host.k3s.lab_exception.expires_on}T00:00:00Z") < 0
      )
      error_message = "The k3s gate requires a clean release or the explicit, unexpired private-lab exception."
    }
  }
}

resource "google_compute_instance" "eps" {
  name                = var.instance_name
  machine_type        = "e2-standard-2"
  zone                = "europe-west3-a"
  deletion_protection = var.deletion_protection
  desired_status      = var.desired_status
  labels = {
    application = "eps"
  }
  boot_disk {
    source      = google_compute_disk.boot.self_link
    auto_delete = false
  }
  network_interface {
    subnetwork = google_compute_subnetwork.eps.id
    # Outbound downloads use an ephemeral address; ingress is restricted separately.
    access_config {}
  }
  service_account {
    email  = google_service_account.eps.email
    scopes = ["cloud-platform"]
  }
  metadata = {
    enable-oslogin           = "TRUE"
    block-project-ssh-keys   = "TRUE"
    disable-legacy-endpoints = "TRUE"
    serial-port-enable       = "FALSE"
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  allow_stopping_for_update = true
  depends_on = [
    google_compute_firewall.iap,
    google_compute_firewall.deny_ingress,
    google_compute_firewall.deny_private_egress,
    google_compute_firewall.web_egress,
    google_compute_firewall.deny_egress,
  ]
}

# Stateful return traffic for IAP SSH does not require an outbound allow rule.
resource "google_compute_firewall" "deny_private_egress" {
  name                    = "eps-lab-deny-private-egress"
  network                 = google_compute_network.eps.self_link
  direction               = "EGRESS"
  priority                = 800
  destination_ranges      = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
  target_service_accounts = [google_service_account.eps.email]
  deny {
    protocol = "all"
  }
}

resource "google_compute_firewall" "web_egress" {
  name                    = "eps-lab-web-egress"
  network                 = google_compute_network.eps.self_link
  direction               = "EGRESS"
  priority                = 900
  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.eps.email]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}

resource "google_compute_firewall" "deny_egress" {
  name                    = "eps-lab-deny-egress"
  network                 = google_compute_network.eps.self_link
  direction               = "EGRESS"
  priority                = 901
  destination_ranges      = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.eps.email]
  deny {
    protocol = "all"
  }
}
