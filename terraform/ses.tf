resource "aws_ses_domain_identity" "main" {
  domain = var.domain_name
}

resource "aws_ses_domain_dkim" "main" {
  domain = aws_ses_domain_identity.main.domain
}

resource "aws_route53_record" "ses_verification" {
  zone_id = aws_route53_zone.main.zone_id
  name    = "_amazonses.${var.domain_name}"
  type    = "TXT"
  ttl     = 600
  records = [aws_ses_domain_identity.main.verification_token]
}

resource "aws_route53_record" "ses_dkim" {
  count   = 3
  zone_id = aws_route53_zone.main.zone_id
  name    = "${aws_ses_domain_dkim.main.dkim_tokens[count.index]}._domainkey.${var.domain_name}"
  type    = "CNAME"
  ttl     = 600
  records = ["${aws_ses_domain_dkim.main.dkim_tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_ses_domain_identity_verification" "main" {
  domain = aws_ses_domain_identity.main.id

  depends_on = [aws_route53_record.ses_verification]
}

resource "aws_ses_receipt_rule_set" "main" {
  rule_set_name = "basketball-scheduler-rules"
}

resource "aws_ses_active_receipt_rule_set" "main" {
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
}

resource "aws_ses_receipt_rule" "admin_email" {
  name          = "store-admin-emails"
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = [var.admin_email]
  enabled       = true
  scan_enabled  = true

  s3_action {
    bucket_name       = aws_s3_bucket.email_inbox.id
    object_key_prefix = "admin/"
    position          = 1
  }

  depends_on = [aws_s3_bucket_policy.allow_ses_put]
}

resource "aws_ses_receipt_rule" "store_in_s3" {
  name          = "store-inbound-emails"
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = [var.domain_name]
  enabled       = true
  scan_enabled  = true
  after         = aws_ses_receipt_rule.admin_email.name

  s3_action {
    bucket_name       = aws_s3_bucket.email_inbox.id
    object_key_prefix = "inbound/"
    position          = 1
  }

  depends_on = [aws_s3_bucket_policy.allow_ses_put]
}
