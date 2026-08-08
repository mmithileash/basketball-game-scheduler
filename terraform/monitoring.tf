# -----------------------------------------------------------------------------
# Monitoring & alerting
#
# Three classes of alert reach the operator's inbox:
#   - cost   — an AWS Budgets monthly cost budget (actual + forecast thresholds)
#   - errors — Lambda Errors, Step Functions failures/timeouts, DynamoDB
#              SystemErrors
#   - usage  — Lambda Throttles + Duration, DynamoDB read/write throttles, SES
#              bounce/complaint reputation
#
# Every CloudWatch alarm publishes to a single regional SNS topic, which emails
# alert_email. The cost budget emails alert_email directly rather than via SNS:
# AWS Budgets is hosted in us-east-1 and can only target a us-east-1 SNS topic,
# whereas this stack may run in any region, so a direct email subscriber keeps
# the budget region-independent.
# -----------------------------------------------------------------------------

locals {
  # Where alerts are delivered. Defaults to the admin inbox when alert_email is
  # left empty.
  alert_email = var.alert_email != "" ? var.alert_email : var.admin_email

  # Every Lambda in the stack, keyed for for_each fan-out of per-function alarms.
  monitored_lambdas = {
    weekly_scheduler        = aws_lambda_function.weekly_scheduler.function_name
    weekly_cutoff_checker   = aws_lambda_function.weekly_cutoff_checker.function_name
    email_processor         = aws_lambda_function.email_processor.function_name
    admin_processor         = aws_lambda_function.admin_processor.function_name
    game_lifecycle_announce = aws_lambda_function.game_lifecycle_announce.function_name
    game_lifecycle_reminder = aws_lambda_function.game_lifecycle_reminder.function_name
    game_lifecycle_confirm  = aws_lambda_function.game_lifecycle_confirm_or_cancel.function_name
    game_lifecycle_finalize = aws_lambda_function.game_lifecycle_finalize.function_name
  }

  monitored_tables = {
    players = aws_dynamodb_table.players.name
    games   = aws_dynamodb_table.games.name
  }
}

# -----------------------------------------------------------------------------
# Alert delivery channel
# -----------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "basketball-alerts"

  tags = {
    Name = "basketball-alerts"
  }
}

# Email subscriptions require a one-time confirmation click on the address
# below before alerts are delivered.
resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = local.alert_email
}

# -----------------------------------------------------------------------------
# Cost — monthly AWS Budgets
# -----------------------------------------------------------------------------

resource "aws_budgets_budget" "monthly_cost" {
  name         = "basketball-monthly-cost"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # 80% of budget already spent this month.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [local.alert_email]
  }

  # Budget fully spent.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [local.alert_email]
  }

  # Forecast to exceed the budget by month end.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [local.alert_email]
  }
}

# -----------------------------------------------------------------------------
# Errors — Lambda
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = local.monitored_lambdas

  alarm_name          = "basketball-${each.key}-errors"
  alarm_description   = "One or more ${each.value} invocations failed in the last 5 minutes."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-${each.key}-errors"
  }
}

# -----------------------------------------------------------------------------
# Usage — Lambda throttles & duration
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = local.monitored_lambdas

  alarm_name          = "basketball-${each.key}-throttles"
  alarm_description   = "${each.value} was throttled (hit a concurrency limit) in the last 5 minutes."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-${each.key}-throttles"
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_duration" {
  for_each = local.monitored_lambdas

  alarm_name          = "basketball-${each.key}-duration"
  alarm_description   = "${each.value} p99 duration exceeded ${var.lambda_duration_alarm_threshold_ms}ms, approaching the 60s timeout."
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  dimensions          = { FunctionName = each.value }
  extended_statistic  = "p99"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.lambda_duration_alarm_threshold_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-${each.key}-duration"
  }
}

# -----------------------------------------------------------------------------
# Errors — Step Functions game lifecycle
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "sfn_executions_failed" {
  alarm_name          = "basketball-game-lifecycle-executions-failed"
  alarm_description   = "A game-lifecycle Step Functions execution failed in the last 5 minutes."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.game_lifecycle.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-game-lifecycle-executions-failed"
  }
}

resource "aws_cloudwatch_metric_alarm" "sfn_executions_timed_out" {
  alarm_name          = "basketball-game-lifecycle-executions-timed-out"
  alarm_description   = "A game-lifecycle Step Functions execution timed out in the last 5 minutes."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsTimedOut"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.game_lifecycle.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-game-lifecycle-executions-timed-out"
  }
}

# -----------------------------------------------------------------------------
# Errors & usage — DynamoDB
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "dynamodb_system_errors" {
  for_each = local.monitored_tables

  alarm_name          = "basketball-dynamodb-${each.key}-system-errors"
  alarm_description   = "DynamoDB table ${each.value} returned server-side (5xx) errors in the last 5 minutes."
  namespace           = "AWS/DynamoDB"
  metric_name         = "SystemErrors"
  dimensions          = { TableName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-dynamodb-${each.key}-system-errors"
  }
}

resource "aws_cloudwatch_metric_alarm" "dynamodb_read_throttles" {
  for_each = local.monitored_tables

  alarm_name          = "basketball-dynamodb-${each.key}-read-throttles"
  alarm_description   = "DynamoDB table ${each.value} throttled read requests in the last 5 minutes."
  namespace           = "AWS/DynamoDB"
  metric_name         = "ReadThrottleEvents"
  dimensions          = { TableName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-dynamodb-${each.key}-read-throttles"
  }
}

resource "aws_cloudwatch_metric_alarm" "dynamodb_write_throttles" {
  for_each = local.monitored_tables

  alarm_name          = "basketball-dynamodb-${each.key}-write-throttles"
  alarm_description   = "DynamoDB table ${each.value} throttled write requests in the last 5 minutes."
  namespace           = "AWS/DynamoDB"
  metric_name         = "WriteThrottleEvents"
  dimensions          = { TableName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-dynamodb-${each.key}-write-throttles"
  }
}

# -----------------------------------------------------------------------------
# Usage — SES sending reputation
#
# The whole system is email-driven, so a rising bounce or complaint rate is the
# earliest warning that AWS may pause sending. These are account-level metrics
# (no dimensions).
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "ses_bounce_rate" {
  alarm_name          = "basketball-ses-bounce-rate"
  alarm_description   = "SES account bounce rate exceeded ${var.ses_bounce_rate_threshold}; AWS may pause sending above 0.05."
  namespace           = "AWS/SES"
  metric_name         = "Reputation.BounceRate"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.ses_bounce_rate_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-ses-bounce-rate"
  }
}

resource "aws_cloudwatch_metric_alarm" "ses_complaint_rate" {
  alarm_name          = "basketball-ses-complaint-rate"
  alarm_description   = "SES account complaint rate exceeded ${var.ses_complaint_rate_threshold}; AWS may pause sending above 0.001."
  namespace           = "AWS/SES"
  metric_name         = "Reputation.ComplaintRate"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.ses_complaint_rate_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "basketball-ses-complaint-rate"
  }
}
