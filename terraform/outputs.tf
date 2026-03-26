output "route53_nameservers" {
  description = "Nameservers for the hosted zone — update your domain registrar with these"
  value       = aws_route53_zone.main.name_servers
}

output "ses_domain_identity_arn" {
  description = "ARN of the SES domain identity"
  value       = aws_ses_domain_identity.main.arn
}

output "lambda_announcement_sender_name" {
  description = "Name of the announcement-sender Lambda function"
  value       = aws_lambda_function.announcement_sender.function_name
}

output "lambda_email_processor_name" {
  description = "Name of the email-processor Lambda function"
  value       = aws_lambda_function.email_processor.function_name
}

output "lambda_reminder_checker_name" {
  description = "Name of the reminder-checker Lambda function"
  value       = aws_lambda_function.reminder_checker.function_name
}

output "dynamodb_players_table_name" {
  description = "Name of the Players DynamoDB table"
  value       = aws_dynamodb_table.players.name
}

output "dynamodb_games_table_name" {
  description = "Name of the Games DynamoDB table"
  value       = aws_dynamodb_table.games.name
}

output "email_bucket_name" {
  description = "Name of the S3 bucket for inbound emails"
  value       = aws_s3_bucket.email_inbox.id
}
