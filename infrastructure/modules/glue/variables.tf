variable "project" {
  description = "Project name, used as the first element of resource names"
  type        = string
}

variable "environment" {
  description = "Deployment environment used in resource names"
  type        = string
}

variable "bucket_name" {
  description = "Name of the data bucket containing raw inputs, artifacts, and processed outputs"
  type        = string
}

variable "data_engineer_role_arn" {
  description = "ARN of the DataEngineer role assumed by Glue"
  type        = string
}

variable "subnet_id" {
  description = "Private subnet in which the Glue job creates network interfaces"
  type        = string
}

variable "security_group_ids" {
  description = "Security groups attached to Glue network interfaces"
  type        = list(string)
}

variable "availability_zone" {
  description = "Availability Zone of the Glue private subnet"
  type        = string
  default     = "us-east-1a"
}
