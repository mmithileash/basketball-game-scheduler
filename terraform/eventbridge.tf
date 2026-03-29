resource "aws_scheduler_schedule" "announcement_sender" {
  name        = "basketball-announcement-sender"
  description = "Triggers announcement-sender Lambda every Monday at 9AM UTC"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 9 ? * MON *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.announcement_sender.arn
    role_arn = aws_iam_role.scheduler_execution.arn
  }
}

resource "aws_scheduler_schedule" "reminder_checker" {
  name        = "basketball-reminder-checker"
  description = "Triggers reminder-checker Lambda every Wednesday and Friday at 9AM UTC"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 9 ? * WED,FRI *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.reminder_checker.arn
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

resource "aws_scheduler_schedule" "game_finalizer" {
  name        = "basketball-game-finalizer"
  description = "Triggers game-finalizer Lambda every Saturday at 13:00 UTC"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 13 ? * SAT *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.game_finalizer.arn
    role_arn = aws_iam_role.scheduler_execution.arn
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
          aws_lambda_function.announcement_sender.arn,
          aws_lambda_function.reminder_checker.arn,
          aws_lambda_function.game_finalizer.arn,
        ]
      }
    ]
  })
}
