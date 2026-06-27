# -----------------------------------------------------------------------------
# Game lifecycle Step Functions state machine
#
# One execution per game, named deterministically "game-{gameDate}" by the
# caller (admin_processor). Drives announce -> reminder -> confirm/cancel ->
# finalize, with a Choice state after each Task that halts the execution if
# the prior task reported the game is no longer OPEN (e.g. admin cancelled it).
# -----------------------------------------------------------------------------

resource "aws_iam_role" "sfn_execution" {
  name = "basketball-sfn-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "basketball-sfn-execution"
  }
}

resource "aws_iam_role_policy" "sfn_invoke_lambda" {
  name = "invoke-game-lifecycle-lambdas"
  role = aws_iam_role.sfn_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = [
          aws_lambda_function.game_lifecycle_announce.arn,
          aws_lambda_function.game_lifecycle_reminder.arn,
          aws_lambda_function.game_lifecycle_confirm_or_cancel.arn,
          aws_lambda_function.game_lifecycle_finalize.arn,
        ]
      }
    ]
  })
}

resource "aws_sfn_state_machine" "game_lifecycle" {
  name     = "basketball-game-lifecycle"
  role_arn = aws_iam_role.sfn_execution.arn

  definition = jsonencode({
    Comment = "Per-game lifecycle: announce -> reminder -> confirm/cancel -> finalize"
    StartAt = "WaitForAnnouncement"
    States = {
      WaitForAnnouncement = {
        Type          = "Wait"
        TimestampPath = "$.announce_at"
        Next          = "AnnounceGame"
      }
      AnnounceGame = {
        Type     = "Task"
        Resource = aws_lambda_function.game_lifecycle_announce.arn
        Parameters = {
          "game_date.$" = "$.game_date"
        }
        ResultPath = "$.task_result"
        Next       = "CheckOpenAfterAnnounce"
      }
      CheckOpenAfterAnnounce = {
        Type = "Choice"
        Choices = [
          {
            Variable      = "$.task_result.game_open"
            BooleanEquals = true
            Next          = "WaitForReminder"
          }
        ]
        Default = "Done"
      }
      WaitForReminder = {
        Type          = "Wait"
        TimestampPath = "$.reminder_at"
        Next          = "SendReminder"
      }
      SendReminder = {
        Type     = "Task"
        Resource = aws_lambda_function.game_lifecycle_reminder.arn
        Parameters = {
          "game_date.$" = "$.game_date"
        }
        ResultPath = "$.task_result"
        Next       = "CheckOpenAfterReminder"
      }
      CheckOpenAfterReminder = {
        Type = "Choice"
        Choices = [
          {
            Variable      = "$.task_result.game_open"
            BooleanEquals = true
            Next          = "WaitForConfirmOrCancel"
          }
        ]
        Default = "Done"
      }
      WaitForConfirmOrCancel = {
        Type          = "Wait"
        TimestampPath = "$.confirm_at"
        Next          = "ConfirmOrCancel"
      }
      ConfirmOrCancel = {
        Type     = "Task"
        Resource = aws_lambda_function.game_lifecycle_confirm_or_cancel.arn
        Parameters = {
          "game_date.$" = "$.game_date"
        }
        ResultPath = "$.task_result"
        Next       = "CheckOpenAfterConfirm"
      }
      CheckOpenAfterConfirm = {
        Type = "Choice"
        Choices = [
          {
            Variable      = "$.task_result.game_open"
            BooleanEquals = true
            Next          = "WaitForFinalize"
          }
        ]
        Default = "Done"
      }
      WaitForFinalize = {
        Type          = "Wait"
        TimestampPath = "$.finalize_at"
        Next          = "FinalizeGame"
      }
      FinalizeGame = {
        Type     = "Task"
        Resource = aws_lambda_function.game_lifecycle_finalize.arn
        Parameters = {
          "game_date.$" = "$.game_date"
        }
        ResultPath = "$.task_result"
        Next       = "Done"
      }
      Done = {
        Type = "Succeed"
      }
    }
  })

  tags = {
    Name = "basketball-game-lifecycle"
  }
}

# -----------------------------------------------------------------------------
# admin_processor -> Step Functions (start/stop game executions)
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "lambda_sfn_control" {
  name = "sfn-start-stop-game-lifecycle"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "states:StartExecution",
        ]
        Resource = aws_sfn_state_machine.game_lifecycle.arn
      },
      {
        Effect = "Allow"
        Action = [
          "states:StopExecution",
        ]
        Resource = "arn:aws:states:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:execution:basketball-game-lifecycle:*"
      }
    ]
  })
}
