variable "project_id" {
  description = "Existing EPS Google Cloud project."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Supply a valid Google Cloud project ID."
  }
}

variable "boot_image" {
  description = "Exact reviewed Debian 13 image, without an image-family alias."
  type        = string
  validation {
    condition     = can(regex("^projects/debian-cloud/global/images/debian-13-[a-z0-9-]+-v[0-9]{8}$", var.boot_image))
    error_message = "Use an exact dated Debian 13 image resource."
  }
}

variable "instance_name" {
  description = "Use a separate name for a replacement acceptance environment."
  type        = string
  default     = "eps-box"
  validation {
    condition     = can(regex("^eps-[a-z0-9-]{1,54}[a-z0-9]$", var.instance_name))
    error_message = "Use a valid instance name beginning with eps-."
  }
}

variable "desired_status" {
  description = "Keep the host stopped outside an attended deployment session."
  type        = string
  default     = "TERMINATED"
  validation {
    condition     = contains(["RUNNING", "TERMINATED"], var.desired_status)
    error_message = "Status must be RUNNING or TERMINATED."
  }
}

variable "deletion_protection" {
  description = "Disable explicitly before an approved instance replacement."
  type        = bool
  default     = true
}
variable "lab_exception" {
  description = "Explicitly select the dated, pinned private-lab exception in docs/lab-security.md."
  type        = bool
  default     = false
}
