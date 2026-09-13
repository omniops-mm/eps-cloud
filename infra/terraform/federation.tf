variable "federation" {
  description = "Public issuer and JWKS for the cloud cluster; null leaves federation disabled."
  type = object({
    issuer = string
    jwks   = string
  })
  default = null
  validation {
    condition = var.federation == null ? true : (
      startswith(var.federation.issuer, "https://") &&
      can(jsondecode(var.federation.jwks).keys) &&
      try(length(jsondecode(var.federation.jwks).keys) > 0 && alltrue([
        for key in jsondecode(var.federation.jwks).keys :
        key.kty == "RSA" && contains(keys(key), "n") && contains(keys(key), "e") &&
        length(setintersection(toset(keys(key)), toset(["d", "p", "q", "dp", "dq", "qi", "oth", "k"]))) == 0
      ]), false)
    )
    error_message = "Federation requires an HTTPS issuer and public RSA verification keys only."
  }
}

locals {
  cloud_secrets = var.federation == null ? {} : {
    eps-app               = "eps"
    eps-db-admin          = "eps"
    eps-postgres-exporter = "eps"
    eps-grafana           = "monitoring"
  }
}

resource "google_iam_workload_identity_pool" "cloud" {
  count                     = var.federation == null ? 0 : 1
  project                   = var.project_id
  workload_identity_pool_id = "eps-cloud-lab"
  display_name              = "EPS private cloud lab"
}

resource "google_iam_workload_identity_pool_provider" "cloud" {
  count                              = var.federation == null ? 0 : 1
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.cloud[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "eps-k3s"
  attribute_mapping                  = { "google.subject" = "assertion.sub" }
  attribute_condition                = "assertion.sub in ['system:serviceaccount:eps:eps-secrets', 'system:serviceaccount:monitoring:eps-secrets']"
  oidc {
    issuer_uri        = var.federation.issuer
    jwks_json         = var.federation.jwks
    allowed_audiences = ["https://iam.googleapis.com/${google_iam_workload_identity_pool.cloud[0].name}/providers/eps-k3s"]
  }
}

resource "google_secret_manager_secret" "cloud" {
  for_each  = local.cloud_secrets
  project   = var.project_id
  secret_id = each.key
  labels    = { environment = "private-lab", application = "eps" }
  replication {
    user_managed {
      replicas {
        location = "europe-west3"
      }
    }
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_secret_manager_secret_iam_member" "cloud" {
  for_each  = local.cloud_secrets
  project   = var.project_id
  secret_id = google_secret_manager_secret.cloud[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.cloud[0].name}/subject/system:serviceaccount:${each.value}:eps-secrets"
}
