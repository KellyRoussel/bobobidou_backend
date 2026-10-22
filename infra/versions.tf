terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }

  # Created once by hand, before the first `terraform init` (see docs/deploiement-gcp.md)
  backend "gcs" {
    bucket = "bobobidou-tfstate"
    prefix = "backend"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
