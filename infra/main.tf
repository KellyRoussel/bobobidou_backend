data "google_project" "this" {}

locals {
  # Known before the service exists, so the OAuth redirect URIs can be set first
  backend_url = "https://${var.service}-${data.google_project.this.number}.${var.region}.run.app"

  # Environment variable of the service => Secret Manager secret.
  # Terraform only creates the secrets: their values never go in the state,
  # they are added by hand in the Secret Manager console
  secrets = {
    SECRET_KEY           = "bobobidou-secret-key"
    DATABASE_URL         = "bobobidou-database-url"
    OPENAI_API_KEY       = "bobobidou-openai-key"
    GOOGLE_CLIENT_SECRET = "bobobidou-google-client-secret"
  }
}

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    # Workload Identity Federation, for GitHub Actions (github.tf)
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}

# --- Service account: only has access to its secrets ---

# Created with gcloud before Terraform
import {
  to = google_service_account.backend
  id = "projects/${var.project_id}/serviceAccounts/${var.service}@${var.project_id}.iam.gserviceaccount.com"
}

resource "google_service_account" "backend" {
  account_id   = var.service
  display_name = "Bobobidou backend (Cloud Run)"
}

resource "google_secret_manager_secret" "app" {
  for_each = local.secrets

  secret_id = each.value
  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "backend" {
  for_each = local.secrets

  secret_id = google_secret_manager_secret.app[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.backend.member
}

# --- Build: `gcloud run deploy --source` builds with the default Compute service account ---

resource "google_project_iam_member" "compute_run_builder" {
  project = var.project_id
  role    = "roles/run.builder"
  member  = "serviceAccount:${data.google_project.this.number}-compute@developer.gserviceaccount.com"
}

# The repository where `gcloud run deploy --source` puts the images. Only 0.5 GB
# are free: keep the last images only
resource "google_artifact_registry_repository" "source_deploy" {
  repository_id = "cloud-run-source-deploy"
  location      = var.region
  format        = "DOCKER"

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-last-3"
    action = "KEEP"
    most_recent_versions {
      keep_count = 3
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "604800s" # 7 days
    }
  }

  depends_on = [google_project_service.apis]
}

# --- Cloud Run service ---

resource "google_cloud_run_v2_service" "backend" {
  name     = var.service
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.backend.email

    # Nothing billed when nobody uses the app; the max caps the bill if something runs away
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      # Placeholder for the first creation: the real image comes from
      # `gcloud run deploy --source`, see ignore_changes
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "GOOGLE_CLIENT_ID"
        value = var.google_client_id
      }
      env {
        name  = "AUTH_REDIRECT_URI"
        value = "${local.backend_url}/auth/callback"
      }

      dynamic "env" {
        for_each = local.secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app[env.key].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    # Set by each `gcloud run deploy`
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
    ]
  }

  depends_on = [google_secret_manager_secret_iam_member.backend]
}

# The app calls the service directly: the authentication is done by our JWTs
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.backend.name
  location = google_cloud_run_v2_service.backend.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
