variable "project_id" {
  type    = string
  default = "bobobidou"
}

variable "region" {
  description = "Belgium: a tier 1 region, the cheapest"
  type        = string
  default     = "europe-west1"
}

variable "service" {
  description = "Name of the Cloud Run service and of its service account"
  type        = string
  default     = "bobobidou-backend"
}

variable "github_repository" {
  description = "Repository allowed to deploy, as owner/name"
  type        = string
  default     = "KellyRoussel/bobobidou_backend"
}

variable "google_client_id" {
  description = "Client ID of the Bobobidou OAuth client (not a secret; the client secret goes in Secret Manager)"
  type        = string
}
