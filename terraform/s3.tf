resource "aws_s3_bucket" "email_inbox" {
  bucket_prefix = "basketball-email-inbox-"
  force_destroy = false

  tags = {
    Name = "basketball-email-inbox"
  }
}

resource "aws_s3_bucket_public_access_block" "email_inbox" {
  bucket = aws_s3_bucket.email_inbox.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "email_inbox" {
  bucket = aws_s3_bucket.email_inbox.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "email_inbox" {
  bucket = aws_s3_bucket.email_inbox.id

  rule {
    id     = "expire-old-emails"
    status = "Enabled"

    expiration {
      days = 90
    }
  }
}

# Allow SES to write to the S3 bucket
resource "aws_s3_bucket_policy" "allow_ses_put" {
  bucket = aws_s3_bucket.email_inbox.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowSESPuts"
        Effect    = "Allow"
        Principal = { Service = "ses.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.email_inbox.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

# S3 event notification to trigger email-processor Lambda on object create
resource "aws_s3_bucket_notification" "email_received" {
  bucket = aws_s3_bucket.email_inbox.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.email_processor.arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_lambda_permission.allow_s3_invoke]
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
