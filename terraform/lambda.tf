locals {
  lambda_functions = {
    weekly_scheduler      = "weekly-scheduler"
    weekly_cutoff_checker = "weekly-cutoff-checker"
    email_processor       = "email-processor"
    admin_processor       = "admin-processor"
  }

  lambda_env_vars = {
    PLAYERS_TABLE       = aws_dynamodb_table.players.name
    GAMES_TABLE         = aws_dynamodb_table.games.name
    EMAIL_BUCKET        = aws_s3_bucket.email_inbox.id
    SENDER_EMAIL        = var.sender_email
    GAME_LOCATION       = var.game_location
    GAME_MAP_URL        = var.game_map_url
    BEDROCK_MODEL_ID    = var.bedrock_model_id
    MIN_PLAYERS         = tostring(var.min_players)
    LONG_GAME_THRESHOLD = tostring(var.long_game_threshold)
    LONG_GAME_START_TIME      = var.long_game_start_time
    LONG_GAME_DURATION_HOURS  = tostring(var.long_game_duration_hours)
    SHORT_GAME_START_TIME     = var.short_game_start_time
    SHORT_GAME_DURATION_HOURS = tostring(var.short_game_duration_hours)
    MAX_GAMES_PER_WEEK  = tostring(var.max_games_per_week)
    ADMIN_EMAIL         = var.admin_email
  }

  lambda_admin_env_vars = merge(local.lambda_env_vars, {
    SENDER_EMAIL           = var.admin_email,
    GAME_LIFECYCLE_SFN_ARN = aws_sfn_state_machine.game_lifecycle.arn,
  })
}

# -----------------------------------------------------------------------------
# Archive data sources — zip each function's source together with common/
# -----------------------------------------------------------------------------

# Build zips that merge function source with common/ directory.
# archive_file cannot merge multiple source directories natively,
# so we copy into a staging dir first, then zip with archive_file.

resource "null_resource" "build_weekly_scheduler" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/weekly_scheduler
      mkdir -p ${path.module}/.build/weekly_scheduler
      cp -r ${path.module}/../src/common ${path.module}/.build/weekly_scheduler/common
      cp -r ${path.module}/../src/weekly_scheduler/* ${path.module}/.build/weekly_scheduler/
      python3 -m pip install -r ${path.module}/../requirements-runtime.txt -t ${path.module}/.build/weekly_scheduler --quiet
    EOT
  }
}

data "archive_file" "weekly_scheduler_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/weekly_scheduler"
  output_path = "${path.module}/.build/weekly_scheduler.zip"

  depends_on = [null_resource.build_weekly_scheduler]
}

resource "null_resource" "build_weekly_cutoff_checker" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/weekly_cutoff_checker
      mkdir -p ${path.module}/.build/weekly_cutoff_checker
      cp -r ${path.module}/../src/common ${path.module}/.build/weekly_cutoff_checker/common
      cp -r ${path.module}/../src/weekly_cutoff_checker/* ${path.module}/.build/weekly_cutoff_checker/
      python3 -m pip install -r ${path.module}/../requirements-runtime.txt -t ${path.module}/.build/weekly_cutoff_checker --quiet
    EOT
  }
}

data "archive_file" "weekly_cutoff_checker_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/weekly_cutoff_checker"
  output_path = "${path.module}/.build/weekly_cutoff_checker.zip"

  depends_on = [null_resource.build_weekly_cutoff_checker]
}

resource "null_resource" "build_game_lifecycle" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/game_lifecycle
      mkdir -p ${path.module}/.build/game_lifecycle
      cp -r ${path.module}/../src/common ${path.module}/.build/game_lifecycle/common
      cp -r ${path.module}/../src/game_lifecycle/* ${path.module}/.build/game_lifecycle/
      python3 -m pip install -r ${path.module}/../requirements-runtime.txt -t ${path.module}/.build/game_lifecycle --quiet
    EOT
  }
}

data "archive_file" "game_lifecycle_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/game_lifecycle"
  output_path = "${path.module}/.build/game_lifecycle.zip"

  depends_on = [null_resource.build_game_lifecycle]
}

resource "null_resource" "build_email_processor" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/email_processor
      mkdir -p ${path.module}/.build/email_processor
      cp -r ${path.module}/../src/common ${path.module}/.build/email_processor/common
      cp -r ${path.module}/../src/email_processor/* ${path.module}/.build/email_processor/
      python3 -m pip install -r ${path.module}/../requirements-runtime.txt -t ${path.module}/.build/email_processor --quiet
    EOT
  }
}

data "archive_file" "email_processor_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/email_processor"
  output_path = "${path.module}/.build/email_processor.zip"

  depends_on = [null_resource.build_email_processor]
}

# -----------------------------------------------------------------------------
# Lambda functions
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "weekly_scheduler" {
  function_name    = "basketball-weekly-scheduler"
  description      = "Prompts admins every Monday to schedule games for the following week"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.weekly_scheduler_zip.output_path
  source_code_hash = data.archive_file.weekly_scheduler_zip.output_base64sha256

  environment {
    # The weekly prompt asks the admin to reply with game dates, so it must be
    # sent FROM admin_email — the reply then routes to admin/ (admin_processor)
    # rather than inbound/ (email_processor).
    variables = merge(local.lambda_env_vars, { SENDER_EMAIL = var.admin_email })
  }

  tags = {
    Name = "basketball-weekly-scheduler"
  }
}

resource "aws_lambda_function" "weekly_cutoff_checker" {
  function_name    = "basketball-weekly-cutoff-checker"
  description      = "Tuesday 9PM UTC cutoff — notifies players of no game if admin hasn't responded"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.weekly_cutoff_checker_zip.output_path
  source_code_hash = data.archive_file.weekly_cutoff_checker_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-weekly-cutoff-checker"
  }
}

resource "aws_lambda_function" "game_lifecycle_announce" {
  function_name    = "basketball-game-lifecycle-announce"
  description      = "SFN task: sends tentative game announcement 7 days before the game"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "announce_task.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.game_lifecycle_zip.output_path
  source_code_hash = data.archive_file.game_lifecycle_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-game-lifecycle-announce"
  }
}

resource "aws_lambda_function" "game_lifecycle_reminder" {
  function_name    = "basketball-game-lifecycle-reminder"
  description      = "SFN task: sends low-signup reminder 4 days before the game"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "reminder_task.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.game_lifecycle_zip.output_path
  source_code_hash = data.archive_file.game_lifecycle_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-game-lifecycle-reminder"
  }
}

resource "aws_lambda_function" "game_lifecycle_confirm_or_cancel" {
  function_name    = "basketball-game-lifecycle-confirm-or-cancel"
  description      = "SFN task: confirms or cancels the game 2 days before, locking in duration"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "confirm_or_cancel_task.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.game_lifecycle_zip.output_path
  source_code_hash = data.archive_file.game_lifecycle_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-game-lifecycle-confirm-or-cancel"
  }
}

resource "aws_lambda_function" "game_lifecycle_finalize" {
  function_name    = "basketball-game-lifecycle-finalize"
  description      = "SFN task: marks the game PLAYED and cleans up guest entries"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "finalize_task.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.game_lifecycle_zip.output_path
  source_code_hash = data.archive_file.game_lifecycle_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-game-lifecycle-finalize"
  }
}

resource "aws_lambda_function" "email_processor" {
  function_name    = "basketball-email-processor"
  description      = "Processes inbound player emails and parses RSVP intent via Bedrock"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.email_processor_zip.output_path
  source_code_hash = data.archive_file.email_processor_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-email-processor"
  }
}

# -----------------------------------------------------------------------------
# Lambda permissions
# -----------------------------------------------------------------------------

resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id   = "AllowS3Invoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.email_processor.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.email_inbox.arn
  source_account = data.aws_caller_identity.current.account_id
}

resource "null_resource" "build_admin_processor" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/admin_processor
      mkdir -p ${path.module}/.build/admin_processor
      cp -r ${path.module}/../src/common ${path.module}/.build/admin_processor/common
      cp -r ${path.module}/../src/admin_processor/* ${path.module}/.build/admin_processor/
      python3 -m pip install -r ${path.module}/../requirements-runtime.txt -t ${path.module}/.build/admin_processor --quiet
    EOT
  }
}

data "archive_file" "admin_processor_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/admin_processor"
  output_path = "${path.module}/.build/admin_processor.zip"

  depends_on = [null_resource.build_admin_processor]
}

resource "aws_lambda_function" "admin_processor" {
  function_name    = "basketball-admin-processor"
  description      = "Processes admin command emails for game cancellation and player management"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.admin_processor_zip.output_path
  source_code_hash = data.archive_file.admin_processor_zip.output_base64sha256

  environment {
    variables = local.lambda_admin_env_vars
  }

  tags = {
    Name = "basketball-admin-processor"
  }
}

resource "aws_lambda_permission" "allow_s3_invoke_admin" {
  statement_id   = "AllowS3InvokeAdmin"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.admin_processor.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.email_inbox.arn
  source_account = data.aws_caller_identity.current.account_id
}
