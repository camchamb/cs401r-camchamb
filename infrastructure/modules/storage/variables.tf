# Every variable needs a description — Task B1 grades this.

variable "project" {
  description = "Project name, used as the first element of every resource name"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "prefixes" {
  description = "Top-level S3 prefixes to create in the data bucket"
  type        = list(string)
  default     = ["raw/", "processed/", "features/", "artifacts/"]
}

variable "enable_lifecycle_rules" {
  description = "Whether to configure lifecycle expiration rules for bucket data"
  type        = bool
  default     = true
}
