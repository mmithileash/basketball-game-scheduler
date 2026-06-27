resource "aws_scheduler_schedule" "weekly_scheduler" {
  name        = "basketball-weekly-scheduler"
  description = "Triggers weekly-scheduler Lambda every Monday at 9AM UTC to prompt admins"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 9 ? * MON *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.weekly_scheduler.arn
    role_arn = aws_iam_role.scheduler_execution.arn
  }
}

resource "aws_scheduler_schedule" "weekly_cutoff_checker" {
  name        = "basketball-weekly-cutoff-checker"
  description = "Triggers weekly-cutoff-checker Lambda every Tuesday at 9PM UTC"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 21 ? * TUE *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.weekly_cutoff_checker.arn
    role_arn = aws_iam_role.scheduler_execution.arn
  }
}

# IAM role for EventBridge Scheduler to invoke Lambda
resource "aws_iam_role" "scheduler_execution" {
  name = "basketball-scheduler-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "basketball-scheduler-execution"
  }
}

resource "aws_iam_role_policy" "scheduler_invoke_lambda" {
  name = "invoke-lambda-functions"
  role = aws_iam_role.scheduler_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = [
          aws_lambda_function.weekly_scheduler.arn,
          aws_lambda_function.weekly_cutoff_checker.arn,
        ]
      }
    ]
  })
}
