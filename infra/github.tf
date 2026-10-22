# --- Deployment from GitHub Actions (.github/workflows/deploy.yml) ---
# Workload Identity Federation: the workflow exchanges its GitHub OIDC token for
# short-lived GCP credentials, no service account key is stored in GitHub

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Only the main branch of this repo can get credentials
  attribute_condition = "assertion.repository == '${var.github_repository}' && assertion.ref == 'refs/heads/main'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions deployer (bobobidou_backend)"
}

resource "google_service_account_iam_member" "deployer_from_github" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

# What `gcloud run deploy --source` needs: upload the sources, start the build,
# read its logs and update the service
resource "google_project_iam_member" "deployer" {
  for_each = toset([
    "roles/run.sourceDeveloper",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/logging.viewer",
  ])

  project = var.project_id
  role    = each.value
  member  = google_service_account.deployer.member
}

# The new revision runs as the backend account, the build as the default Compute account
resource "google_service_account_iam_member" "deployer_act_as_backend" {
  service_account_id = google_service_account.backend.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

resource "google_service_account_iam_member" "deployer_act_as_compute" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${data.google_project.this.number}-compute@developer.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}
