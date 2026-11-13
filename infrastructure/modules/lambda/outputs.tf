output "canary_lambda_arn" {
  description = "ARN of the canary deployment Lambda function"
  value       = aws_lambda_function.canary_deployment.arn
}

output "canary_lambda_name" {
  description = "Name of the canary deployment Lambda function"
  value       = aws_lambda_function.canary_deployment.function_name
}

output "model_approval_lambda_name" {
  value = aws_lambda_function.canary_deployment.function_name
}