# -----------------------------------------------------------------------------
# Lambda execution role
# -----------------------------------------------------------------------------

resource "aws_iam_role" "lambda_execution" {
  name = "basketball-lambda-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "basketball-lambda-execution"
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Logs — allow Lambda to create log groups and write logs
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "lambda_logging" {
  name = "cloudwatch-logs"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/basketball-*:*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# DynamoDB — read/write on both tables
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "lambda_dynamodb" {
  name = "dynamodb-access"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:BatchGetItem",
          "dynamodb:BatchWriteItem",
          "dynamodb:TransactWriteItems",
          "dynamodb:TransactGetItems",
        ]
        Resource = [
          aws_dynamodb_table.players.arn,
          aws_dynamodb_table.games.arn,
        ]
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# S3 — read objects from the email bucket
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "lambda_s3" {
  name = "s3-read-email-bucket"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.email_inbox.arn,
          "${aws_s3_bucket.email_inbox.arn}/*",
        ]
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# SES — send email
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "lambda_ses" {
  name = "ses-send-email"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail",
        ]
        Resource = [
          "arn:aws:ses:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:identity/*",
        ]
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Bedrock — invoke model
# -----------------------------------------------------------------------------

resource "aws_iam_role_policy" "lambda_bedrock" {
  name = "bedrock-invoke-model"
  role = aws_iam_role.lambda_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
        ]
        Resource = "arn:aws:bedrock:${data.aws_region.current.name}::foundation-model/${var.bedrock_model_id}"
      },
      {
        Effect = "Allow"
        Action = [
          "aws-marketplace:ViewSubscriptions",
          "aws-marketplace:Subscribe",
        ]
        Resource = "*"
      }
    ]
  })
}
