mock_provider "google" {}

variables {
  project_id    = "eps-validation"
  boot_image    = "projects/debian-cloud/global/images/debian-13-trixie-v20260908"
  lab_exception = true
  federation = {
    issuer = "https://kubernetes.default.svc.cluster.local"
    jwks   = "{\"keys\":[{\"kty\":\"RSA\",\"n\":\"AQAB\",\"e\":\"AQAB\"}]}"
  }
}

run "namespace_scoped_secret_access" {
  command = plan
  assert {
    condition = length(google_secret_manager_secret.cloud) == 4 && local.cloud_secrets["eps-grafana"] == "monitoring" && local.cloud_secrets["eps-app"] == "eps" && strcontains(google_iam_workload_identity_pool_provider.cloud[0].attribute_condition, "system:serviceaccount:monitoring:eps-secrets")
    error_message = "Secret access must remain scoped to the two explicit namespace identities."
  }
}

run "private_signing_material_rejected" {
  command = plan
  variables {
    federation = {
      issuer = "https://kubernetes.default.svc.cluster.local"
      jwks   = "{\"keys\":[{\"kty\":\"RSA\",\"n\":\"AQAB\",\"e\":\"AQAB\",\"d\":\"private\"}]}"
    }
  }
  expect_failures = [var.federation]
}
