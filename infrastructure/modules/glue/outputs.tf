output "database_name" {
  description = "Name of the Glue Data Catalog database"
  value       = aws_glue_catalog_database.this.name
}

output "crawler_name" {
  description = "Name of the raw-data Glue crawler"
  value       = aws_glue_crawler.raw.name
}

output "transform_job_name" {
  description = "Name of the Glue transform job"
  value       = aws_glue_job.transform.name
}
