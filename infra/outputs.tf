output "backend_url" {
  value = local.backend_url
}

output "service_account" {
  value = google_service_account.backend.email
}
