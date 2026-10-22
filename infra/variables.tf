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

# Ids of the repository and of its owner: unlike the names, they never change
# (gh api repos/KellyRoussel/bobobidou_backend --jq '.id, .owner.id')
variable "github_repository_id" {
  type    = string
  default = "1427395397"
}

variable "github_repository_owner_id" {
  type    = string
  default = "37748254"
}

variable "google_client_id" {
  description = "Client ID of the Bobobidou OAuth client (not a secret; the client secret goes in Secret Manager)"
  type        = string
}
