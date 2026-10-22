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
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
  }
  # Only the deploy workflow, run from main by a push or by hand, in the
  # production environment. The repository and its owner are checked by their
  # ids, which don't change, rather than by their names, which can be taken
  # over by someone else after a rename
  attribute_condition = join(" && ", [
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.repository_owner_id == '${var.github_repository_owner_id}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.workflow_ref == '${var.github_repository}/.github/workflows/deploy.yml@refs/heads/main'",
    "assertion.event_name in ['push', 'workflow_dispatch']",
    "assertion.environment == 'production'",
  ])

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
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
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

# The new revision runs as the backend account, the build as the builder account
resource "google_service_account_iam_member" "deployer_act_as_backend" {
  service_account_id = google_service_account.backend.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

resource "google_service_account_iam_member" "deployer_act_as_builder" {
  service_account_id = google_service_account.builder.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}
