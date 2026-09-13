output "instance_name" {
  description = "Instance selected for IAP access."
  value       = google_compute_instance.eps.name
}

output "zone" {
  value = google_compute_instance.eps.zone
}

output "boot_disk" {
  description = "Retained disk; deleting the instance does not remove this resource."
  value       = google_compute_disk.boot.name
}
