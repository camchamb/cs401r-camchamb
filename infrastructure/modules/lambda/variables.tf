
variable "project" {
  description = "Project name, used as the first element of every resource name"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS deployment region"
  type        = string
  default     = "us-east-1"
}

variable "lambda_execution_role_arn" {
  description = "ARN of an existing IAM role trusted by Lambda"
  type        = string
}

variable "sagemaker_execution_role_arn" {
  description = "IAM role SageMaker uses to access model artifacts"
  type        = string
}

variable "endpoint_name" {
  description = "Name of the existing SageMaker endpoint to update"
  type        = string
}

variable "codepipeline_name" {
  description = "Name of the CodePipeline allowed to invoke the function"
  type        = string
}

variable "canary_max_minutes" {
  description = "Maximum time the canary may receive traffic before automatic rollback"
  type        = number
  default     = 60
}

variable "rollback_alarm_name" {
  description = "CloudWatch alarm that must be OK before the canary is promoted"
  type        = string
  default     = "northstar-churn-rollback-model-latency"
}
