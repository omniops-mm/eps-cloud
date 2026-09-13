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

data "google_compute_network" "default" {
  name = "default"
}

resource "google_service_account" "eps" {
  account_id   = "eps-box"
  display_name = "EPS host"
  # Workload federation grants application access; the host receives no project roles.
}

resource "google_compute_firewall" "iap" {
  name          = "allow-iap-ssh"
  network       = data.google_compute_network.default.self_link
  priority      = 900
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["eps-iap"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "deny_ingress" {
  name                    = "deny-eps-ingress"
  network                 = data.google_compute_network.default.self_link
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
      condition     = local.host.k3s.approved
      error_message = "The recorded k3s security gate is closed. Do not provision an idle host."
    }
  }
}

resource "google_compute_instance" "eps" {
  name                = var.instance_name
  machine_type        = "e2-standard-2"
  zone                = "europe-west3-a"
  deletion_protection = var.deletion_protection
  desired_status      = var.desired_status
  tags                = ["eps-iap"]
  labels = {
    application = "eps"
  }
  boot_disk {
    source      = google_compute_disk.boot.self_link
    auto_delete = false
  }
  network_interface {
    network = data.google_compute_network.default.self_link
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
  ]
}
