output "route53_nameservers" {
  description = "Nameservers for the hosted zone — update your domain registrar with these"
  value       = aws_route53_zone.main.name_servers
}

output "ses_domain_identity_arn" {
  description = "ARN of the SES domain identity"
  value       = aws_ses_domain_identity.main.arn
}

output "lambda_weekly_scheduler_name" {
  description = "Name of the weekly-scheduler Lambda function"
  value       = aws_lambda_function.weekly_scheduler.function_name
}

output "lambda_weekly_cutoff_checker_name" {
  description = "Name of the weekly-cutoff-checker Lambda function"
  value       = aws_lambda_function.weekly_cutoff_checker.function_name
}

output "lambda_email_processor_name" {
  description = "Name of the email-processor Lambda function"
  value       = aws_lambda_function.email_processor.function_name
}

output "game_lifecycle_state_machine_arn" {
  description = "ARN of the per-game Step Functions state machine"
  value       = aws_sfn_state_machine.game_lifecycle.arn
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
