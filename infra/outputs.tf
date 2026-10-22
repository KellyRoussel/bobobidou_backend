output "backend_url" {
  value = local.backend_url
}

output "service_account" {
  value = google_service_account.backend.email
}

# GitHub repository variables used by .github/workflows/deploy.yml
output "github_workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "github_deployer_service_account" {
  value = google_service_account.deployer.email
}
