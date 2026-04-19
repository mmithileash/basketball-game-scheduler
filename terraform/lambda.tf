locals {
  lambda_functions = {
    announcement_sender = "announcement-sender"
    email_processor     = "email-processor"
    reminder_checker    = "reminder-checker"
    game_finalizer      = "game-finalizer"
    admin_processor     = "admin-processor"
  }

  lambda_env_vars = {
    PLAYERS_TABLE    = aws_dynamodb_table.players.name
    GAMES_TABLE      = aws_dynamodb_table.games.name
    EMAIL_BUCKET     = aws_s3_bucket.email_inbox.id
    SENDER_EMAIL     = var.sender_email
    GAME_TIME        = var.game_time
    GAME_LOCATION    = var.game_location
    BEDROCK_MODEL_ID = var.bedrock_model_id
    MIN_PLAYERS      = tostring(var.min_players)
    ADMIN_EMAIL = var.admin_email
  }

  lambda_admin_env_vars = merge(local.lambda_env_vars, {
    SENDER_EMAIL = var.admin_email,
  })
}

# -----------------------------------------------------------------------------
# Archive data sources — zip each function's source together with common/
# -----------------------------------------------------------------------------

# Build zips that merge function source with common/ directory.
# archive_file cannot merge multiple source directories natively,
# so we copy into a staging dir first, then zip with archive_file.

resource "null_resource" "build_announcement_sender" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/announcement_sender
      mkdir -p ${path.module}/.build/announcement_sender
      cp -r ${path.module}/../src/common ${path.module}/.build/announcement_sender/common
      cp -r ${path.module}/../src/announcement_sender/* ${path.module}/.build/announcement_sender/
      python3 -m pip install -r ${path.module}/../requirements-runtime.txt -t ${path.module}/.build/announcement_sender --quiet
    EOT
  }
}

data "archive_file" "announcement_sender_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/announcement_sender"
  output_path = "${path.module}/.build/announcement_sender.zip"

  depends_on = [null_resource.build_announcement_sender]
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

resource "null_resource" "build_reminder_checker" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/reminder_checker
      mkdir -p ${path.module}/.build/reminder_checker
      cp -r ${path.module}/../src/common ${path.module}/.build/reminder_checker/common
      cp -r ${path.module}/../src/reminder_checker/* ${path.module}/.build/reminder_checker/
      python3 -m pip install -r ${path.module}/../requirements-runtime.txt -t ${path.module}/.build/reminder_checker --quiet
    EOT
  }
}

data "archive_file" "reminder_checker_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/reminder_checker"
  output_path = "${path.module}/.build/reminder_checker.zip"

  depends_on = [null_resource.build_reminder_checker]
}

# -----------------------------------------------------------------------------
# Lambda functions
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "announcement_sender" {
  function_name    = "basketball-announcement-sender"
  description      = "Sends weekly game announcements every Monday"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.announcement_sender_zip.output_path
  source_code_hash = data.archive_file.announcement_sender_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-announcement-sender"
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

resource "aws_lambda_function" "reminder_checker" {
  function_name    = "basketball-reminder-checker"
  description      = "Checks player count and sends reminders on Wed/Fri if minimum not met"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.reminder_checker_zip.output_path
  source_code_hash = data.archive_file.reminder_checker_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-reminder-checker"
  }
}

resource "null_resource" "build_game_finalizer" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/game_finalizer
      mkdir -p ${path.module}/.build/game_finalizer
      cp -r ${path.module}/../src/common ${path.module}/.build/game_finalizer/common
      cp -r ${path.module}/../src/game_finalizer/* ${path.module}/.build/game_finalizer/
      python3 -m pip install -r ${path.module}/../requirements-runtime.txt -t ${path.module}/.build/game_finalizer --quiet
    EOT
  }
}

data "archive_file" "game_finalizer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/game_finalizer"
  output_path = "${path.module}/.build/game_finalizer.zip"

  depends_on = [null_resource.build_game_finalizer]
}

resource "aws_lambda_function" "game_finalizer" {
  function_name    = "basketball-game-finalizer"
  description      = "Marks Saturday games as PLAYED at 13:00 UTC"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.game_finalizer_zip.output_path
  source_code_hash = data.archive_file.game_finalizer_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-game-finalizer"
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
