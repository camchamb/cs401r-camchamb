terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  required_version = ">= 1.5.0"
}

locals {
  database_name = replace("${var.project}_${var.environment}", "-", "_")
  script_key    = "artifacts/glue/transform.py"
}

resource "aws_glue_catalog_database" "this" {
  name = local.database_name
}

resource "aws_glue_crawler" "raw" {
  name          = "${var.project}-${var.environment}-raw-crawler"
  database_name = aws_glue_catalog_database.this.name
  role          = var.data_engineer_role_arn
  table_prefix  = ""

  s3_target {
    path = "s3://${var.bucket_name}/raw/customers/"
  }

  schema_change_policy {
    delete_behavior = "LOG"
    update_behavior = "UPDATE_IN_DATABASE"
  }
}

resource "aws_s3_object" "transform_script" {
  bucket = var.bucket_name
  key    = local.script_key
  source = "${path.module}/../../../glue-scripts/transform.py"
  etag   = filemd5("${path.module}/../../../glue-scripts/transform.py")
}

resource "aws_glue_connection" "private" {
  name            = "${var.project}-${var.environment}-glue-private"
  connection_type = "NETWORK"

  physical_connection_requirements {
    availability_zone      = var.availability_zone
    security_group_id_list = var.security_group_ids
    subnet_id              = var.subnet_id
  }
}

resource "aws_glue_job" "transform" {
  name              = "${var.project}-${var.environment}-transform"
  role_arn          = var.data_engineer_role_arn
  glue_version      = "4.0"
  number_of_workers = 2
  worker_type       = "G.1X"
  connections       = [aws_glue_connection.private.name]

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${var.bucket_name}/${aws_s3_object.transform_script.key}"
  }

  default_arguments = {
    "--job-language"                     = "python"
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--database_name"                    = aws_glue_catalog_database.this.name
    "--table_name"                       = "customers"
    "--output_path"                      = "s3://${var.bucket_name}/processed/customers/"
  }
}
