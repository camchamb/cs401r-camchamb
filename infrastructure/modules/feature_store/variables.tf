variable "project" {
  description = "Project name, used as the first element of resource names"
  type        = string
}

variable "environment" {
  description = "Deployment environment used in resource names"
  type        = string
}

variable "bucket_name" {
  description = "Data bucket that hosts the offline Feature Store"
  type        = string
}

variable "execution_role_arn" {
  description = "DataEngineer role ARN used by SageMaker Feature Store"
  type        = string
}
