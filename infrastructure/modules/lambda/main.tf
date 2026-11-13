
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# Package the Lambda Python file.
data "archive_file" "canary_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../../deployment/lambda/lambda_function.py"
  output_path = "${path.module}/canary_lambda.zip"
}

# Deploy Lambda
resource "aws_lambda_function" "canary_deployment" {
  function_name = "lambda-canary-deployment"

  filename         = data.archive_file.canary_lambda.output_path
  source_code_hash = data.archive_file.canary_lambda.output_base64sha256

  role    = var.lambda_execution_role_arn
  handler = "lambda_function.lambda_handler"
  runtime = "python3.12"
  timeout = 300

  environment {
    variables = {
      ENDPOINT_NAME                = var.endpoint_name
      SAGEMAKER_EXECUTION_ROLE_ARN = var.sagemaker_execution_role_arn
      SCHEDULER_ROLE_ARN           = aws_iam_role.rollback_scheduler.arn
      CANARY_MAX_MINUTES           = tostring(var.canary_max_minutes)
    }
  }
}

# EventBridge Scheduler assumes this role for the one-time, 60-minute rollback
# invocation. The schedule deletes itself after it runs.
resource "aws_iam_role" "rollback_scheduler" {
  name = "${var.project}-${var.environment}-CanaryRollbackScheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "scheduler.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "rollback_scheduler" {
  name = "InvokeCanaryRollbackLambda"
  role = aws_iam_role.rollback_scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = aws_lambda_function.canary_deployment.arn
    }]
  })
}

# Allow CodePipeline to invoke the Lambda
resource "aws_lambda_permission" "allow_codepipeline" {
  statement_id  = "AllowCodePipelineInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.canary_deployment.function_name
  principal     = "codepipeline.amazonaws.com"
  source_arn = format(
    "arn:%s:codepipeline:%s:%s:%s",
    data.aws_partition.current.partition,
    var.aws_region,
    data.aws_caller_identity.current.account_id,
    var.codepipeline_name,
  )
}
