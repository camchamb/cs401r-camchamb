# ── modules/iam ──────────────────────────────────────────────────────────────
# Required resources (Task B1). Exactly one of each:
#
#   aws_iam_role                     MLEngineer, trusted by sagemaker.amazonaws.com
#   aws_iam_policy
#   aws_iam_role_policy_attachment
#
# Least privilege is graded in later labs, so start narrow: grant only the S3
# prefixes and SageMaker actions this role actually needs. A wildcard policy
# here will cost you points in Lab 2.

# TODO: implement the three resources above.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  required_version = ">= 1.5.0"
}

# provider "aws" {
#   region = "us-east-1"
# }

# ------------------------------------------------------------
# SageMaker IAM Role
# ------------------------------------------------------------

resource "aws_iam_role" "ml_engineer" {
  name = "${var.project}-${var.environment}-MLEngineer"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Effect = "Allow"

        Principal = {
          Service = "sagemaker.amazonaws.com"
        }

        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name        = "${var.project}-${var.environment}-MLEngineer"
    Project     = var.project
    Environment = var.environment
  }
}

# ------------------------------------------------------------
# MLEngineer Inline Policy
# ------------------------------------------------------------

resource "aws_iam_role_policy" "ml_engineer" {
  name = "${var.project}-${var.environment}-MLEngineerPolicy"
  role = aws_iam_role.ml_engineer.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sagemaker:AddTags"
        ]
        Resource = [
          "arn:aws:sagemaker:us-east-1:061624868577:training-job/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "sagemaker:AddTags",
          "sagemaker:DescribeTrainingJob"
        ]
        Resource = [
          "arn:aws:sagemaker:us-east-1:061624868577:training-job/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "sagemaker:AddTags",
          "sagemaker:CreateModelPackageGroup",
          "sagemaker:DescribeModelPackageGroup"
        ]
        Resource = [
          "arn:aws:sagemaker:us-east-1:061624868577:model-package-group/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "sagemaker:AddTags",
          "sagemaker:CreateModelPackage",
          "sagemaker:DescribeModelPackage",
          "sagemaker:ListModelPackages",
          "sagemaker:UpdateModelPackage"
        ]
        Resource = [
          "arn:aws:sagemaker:us-east-1:061624868577:model-package/*",
          "arn:aws:sagemaker:us-east-1:061624868577:model-package-group/*"
        ]
      },
      {
        Sid    = "SageMakerCore"
        Effect = "Allow"

        Action = [
          "sagemaker:CreateTrainingJob",
          "sagemaker:DescribeTrainingJob",
          "sagemaker:StopTrainingJob",
          "sagemaker:CreateEndpoint",
          "sagemaker:DescribeEndpoint",
          "sagemaker:DeleteEndpoint",
          "sagemaker:CreateEndpointConfig",
          "sagemaker:DeleteEndpointConfig",
          "sagemaker:CreateMlflowApp",
          "sagemaker:DescribeMlflowApp",
          "sagemaker:ListMlflowApps",
          "sagemaker:CreatePresignedMlflowAppUrl",
          "sagemaker:RegisterModel",
          "sagemaker:DescribeModelPackage",
          "sagemaker:ListModelPackages"
        ]

        Resource = "*"
      },

      {
        Sid    = "StudioSelfService"
        Effect = "Allow"

        Action = [
          "sagemaker:DescribeDomain",
          "sagemaker:ListDomains",
          "sagemaker:DescribeUserProfile",
          "sagemaker:ListUserProfiles",
          "sagemaker:DescribeSpace",
          "sagemaker:ListSpaces",
          "sagemaker:CreateSpace",
          "sagemaker:UpdateSpace",
          "sagemaker:DeleteSpace",
          "sagemaker:DescribeApp",
          "sagemaker:ListApps",
          "sagemaker:CreateApp",
          "sagemaker:DeleteApp",
          "sagemaker:CreatePresignedDomainUrl"
        ]

        Resource = [
          "arn:aws:sagemaker:*:*:domain/*",
          "arn:aws:sagemaker:*:*:user-profile/*",
          "arn:aws:sagemaker:*:*:space/*",
          "arn:aws:sagemaker:*:*:app/*"
        ]
      },

      {
        Sid    = "S3ArtifactsAndFeatures"
        Effect = "Allow"

        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]

        Resource = [
          "arn:aws:s3:::northstar-dev-data-*/artifacts/*",
          "arn:aws:s3:::northstar-dev-data-*/features/*"
        ]
      },

      {
        Sid    = "S3DataCaptureWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:AbortMultipartUpload"
        ]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-data-*/datacapture/*"
      },

      {
        Sid       = "PassSelfToSageMaker"
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = aws_iam_role.ml_engineer.arn
        Condition = { StringEquals = { "iam:PassedToService" = "sagemaker.amazonaws.com" } }
      },

      {
        Sid    = "S3BucketList"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = "arn:aws:s3:::northstar-dev-data-*"
      },

      {
        Sid      = "EndpointInstanceMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = "/aws/sagemaker/Endpoints" }
        }
      },

      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"

        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]

        Resource = "arn:aws:logs:*:*:log-group:/aws/sagemaker/*"
      },

      {
        Sid    = "ECRRead"
        Effect = "Allow"

        Action = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:GetAuthorizationToken"
        ]

        Resource = "*"
      }
    ]
  })
}

# ------------------------------------------------------------
# Data Engineer IAM Role
# ------------------------------------------------------------

resource "aws_iam_role" "data_engineer" {
  name = "${var.project}-${var.environment}-DataEngineer"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = [
          "glue.amazonaws.com",
          "lambda.amazonaws.com",
          "sagemaker.amazonaws.com"
        ]
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = {
    Name        = "${var.project}-${var.environment}-DataEngineer"
    Project     = var.project
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "data_engineer" {
  name = "${var.project}-${var.environment}-DataEngineerPolicy"
  role = aws_iam_role.data_engineer.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "S3DataPrefixes"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/raw/*",
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/processed/*",
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/features/*",
          # Spark creates these legacy S3 directory markers beside the output prefixes.
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/processed_$folder$",
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/features_$folder$"
        ]
      },
      {
        Sid      = "ReadGlueArtifacts"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-data-*/artifacts/glue/*"
      },
      {
        Sid      = "ListDataBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-data-*"
      },
      {
        Sid      = "FeatureStoreBucketPermissions"
        Effect   = "Allow"
        Action   = ["s3:GetBucketAcl"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-data-*"
      },
      {
        Sid      = "FeatureStoreObjectAcl"
        Effect   = "Allow"
        Action   = ["s3:PutObjectAcl"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-data-*/features/*"
      },
      {
        Sid    = "GlueCatalogAndJobs"
        Effect = "Allow"
        Action = [
          "glue:CreateDatabase", "glue:GetDatabase", "glue:GetDatabases",
          "glue:CreateTable", "glue:GetTable", "glue:GetTables", "glue:UpdateTable", "glue:DeleteTable",
          "glue:GetPartition", "glue:GetPartitions", "glue:CreatePartition", "glue:BatchCreatePartition",
          "glue:UpdatePartition", "glue:DeletePartition", "glue:BatchDeletePartition", "ec2:DescribeRouteTables",
          "glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:GetConnection", "ec2:DescribeVpcEndpoints"
        ]
        Resource = "*"
      },
      {
        Sid      = "FeatureStoreIngestion"
        Effect   = "Allow"
        Action   = ["sagemaker:DescribeFeatureGroup", "sagemaker:PutRecord"]
        Resource = "*"
      },
      {
        Sid    = "GlueVpcNetworking"
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface", "ec2:DeleteNetworkInterface", "ec2:DescribeNetworkInterfaces",
          "ec2:DescribeVpcs", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups"
        ]
        Resource = "*"
      },
      {
        Sid      = "GlueLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:log-group:/aws-glue/*"
      },
      {
        Sid    = "CanaryDeployment"
        Effect = "Allow"
        Action = [
          "sagemaker:AddTags",
          "sagemaker:CreateEndpointConfig",
          "sagemaker:CreateModel",
          "sagemaker:DescribeEndpoint",
          "sagemaker:DescribeEndpointConfig",
          "sagemaker:DescribeModel",
          "sagemaker:DescribeModelPackage",
          "sagemaker:UpdateModelPackage",
          "sagemaker:UpdateEndpoint",
          "sagemaker:UpdateEndpointWeightsAndCapacities"
        ]
        Resource = "*"
      },
      {
        Sid    = "ReportCodePipelineJobResult"
        Effect = "Allow"
        Action = [
          "codepipeline:PutJobFailureResult",
          "codepipeline:PutJobSuccessResult"
        ]
        Resource = "*"
      },
      {
        Sid       = "PassMLEngineerRoleToSageMaker"
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = aws_iam_role.ml_engineer.arn
        Condition = { StringEquals = { "iam:PassedToService" = "sagemaker.amazonaws.com" } }
      },
      {
        Sid    = "ManageCanaryRollbackSchedule"
        Effect = "Allow"
        Action = [
          "scheduler:CreateSchedule",
          "scheduler:DeleteSchedule"
        ]
        Resource = "arn:aws:scheduler:*:*:schedule/default/northstar-canary-decision-*"
      },
      {
        Sid      = "ReadCanaryRollbackAlarm"
        Effect   = "Allow"
        Action   = ["cloudwatch:DescribeAlarms"]
        Resource = "*"
      },
      {
        Sid      = "GlueJobMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = "Glue" }
        }
      },
      {
        Sid       = "PassCanaryRollbackSchedulerRole"
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = "arn:aws:iam::*:role/${var.project}-${var.environment}-CanaryRollbackScheduler"
        Condition = { StringEquals = { "iam:PassedToService" = "scheduler.amazonaws.com" } }
      },
      {
        Sid    = "CanaryLambdaLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:log-group:/aws/lambda/lambda-canary-deployment:*"
      },
      {
        Sid      = "GlueNetworkInterfaceTags"
        Effect   = "Allow"
        Action   = ["ec2:CreateTags", "ec2:DeleteTags"]
        Resource = "arn:aws:ec2:*:*:network-interface/*"
      }
    ]
  })
}

# ------------------------------------------------------------
# Model Monitor IAM Role
# ------------------------------------------------------------

resource "aws_iam_role" "model_monitor" {
  name = "${var.project}-${var.environment}-ModelMonitor"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "sagemaker.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = {
    Name        = "${var.project}-${var.environment}-ModelMonitor"
    Project     = var.project
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "model_monitor" {
  name = "${var.project}-${var.environment}-ModelMonitorPolicy"
  role = aws_iam_role.model_monitor.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "ReadProcessingJobs"
        Effect   = "Allow"
        Action   = ["sagemaker:DescribeProcessingJob", "sagemaker:ListProcessingJobs"]
        Resource = "*"
      },
      {
        Sid      = "ReadArtifacts"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-data-*/artifacts/*"
      },
      {
        Sid      = "ListDataBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-data-*"
      },
      {
        Sid      = "MonitoringMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      },
      {
        Sid      = "MonitoringLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:log-group:/aws/sagemaker/*"
      }
    ]
  })
}

# ── ModelMonitorExecution (Lab 6) ────────────────────────────────────────────
resource "aws_iam_role" "model_monitor_execution" {
  name = "${var.project}-${var.environment}-ModelMonitorExecution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name        = "${var.project}-${var.environment}-ModelMonitorExecution"
    Project     = var.project
    Environment = var.environment
  }
}

resource "aws_iam_policy" "model_monitor_execution" {
  name        = "${var.project}-${var.environment}-ModelMonitorExecutionPolicy"
  description = "Evidently drift job: pull the sklearn image, read datacapture and monitoring, write monitoring reports"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRPullProcessingImage"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = "*"
      },
      {
        Sid      = "S3BucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-data-*"
      },
      {
        Sid    = "ReadCaptureBaselineAndCode"
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/datacapture/*",
          "arn:aws:s3:::${var.project}-${var.environment}-data-*/monitoring/*"
        ]
      },
      {
        Sid      = "WriteMonitoringReports"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "arn:aws:s3:::${var.project}-${var.environment}-data-*/monitoring/*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:aws:logs:*:*:log-group:/aws/sagemaker/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "model_monitor_execution" {
  role       = aws_iam_role.model_monitor_execution.name
  policy_arn = aws_iam_policy.model_monitor_execution.arn
}
