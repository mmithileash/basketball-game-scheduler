variable "aws_region" {
  description = "AWS region to deploy into. Must be a region where SES inbound email and the configured Bedrock model are available."
  type        = string
  default     = "us-east-1"
}

variable "domain_name" {
  description = "Domain name for SES email and Route 53 hosted zone (e.g. hoops.example.com)"
  type        = string
}

variable "sender_email" {
  description = "Email address used to send game announcements and reminders (e.g. scheduler@hoops.example.com)"
  type        = string
}

variable "game_location" {
  description = "Default game location displayed in announcements"
  type        = string
  default     = "TBD"
}

variable "game_map_url" {
  description = "Optional Google Maps (or similar) URL for the game location. When set, the location is rendered as a clickable link with the address as its text; leave empty to show the plain address."
  type        = string
  default     = ""
}

variable "bedrock_model_id" {
  description = "AWS Bedrock inference profile ID for NLU intent parsing. Claude Haiku 4.5 is not available with on-demand throughput and must be invoked via a cross-region inference profile (e.g. 'us.' prefix for US regions, 'eu.' for EU regions)."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "min_players" {
  description = "Minimum number of confirmed players required for a game to proceed"
  type        = number
  default     = 6
}

variable "long_game_threshold" {
  description = "Confirmed-player count at/above which the long-game tier applies (seeds each game's policy threshold)"
  type        = number
  default     = 10
}

variable "long_game_start_time" {
  description = "Default long-game (well-attended) start time, display string (seeds each game's policy)"
  type        = string
  default     = "10:00 AM"
}

variable "long_game_duration_hours" {
  description = "Default long-game (well-attended) duration in hours (seeds each game's policy)"
  type        = number
  default     = 2
}

variable "short_game_start_time" {
  description = "Default short-game (thinly-attended) start time, display string (seeds each game's policy)"
  type        = string
  default     = "11:00 AM"
}

variable "short_game_duration_hours" {
  description = "Default short-game (thinly-attended) duration in hours (seeds each game's policy)"
  type        = number
  default     = 1
}

variable "max_games_per_week" {
  description = "Maximum games per week before the Monday admin prompt is suppressed"
  type        = number
  default     = 1
}

variable "environment" {
  description = "Environment tag (e.g. dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "admin_email" {
  description = "Email address for admin commands (e.g. admin@hoops.example.com)"
  type        = string
}

# -----------------------------------------------------------------------------
# Monitoring & alerting
# -----------------------------------------------------------------------------

variable "alert_email" {
  description = "Email address that receives cost, error, and usage alerts. Leave empty to fall back to admin_email."
  type        = string
  default     = ""
}

variable "monthly_budget_limit_usd" {
  description = "Monthly AWS cost budget in USD. Budget notifications fire at 80% / 100% of actual spend and at 100% of forecast spend."
  type        = number
  default     = 50
}

variable "lambda_duration_alarm_threshold_ms" {
  description = "Lambda duration (p99, ms) above which a usage alarm fires. Functions have a 60s (60000ms) timeout, so the default warns well before timeouts begin."
  type        = number
  default     = 45000
}

variable "ses_bounce_rate_threshold" {
  description = "SES account bounce rate (0-1) above which an alarm fires. AWS may pause sending above 0.05."
  type        = number
  default     = 0.05
}

variable "ses_complaint_rate_threshold" {
  description = "SES account complaint rate (0-1) above which an alarm fires. AWS may pause sending above 0.001."
  type        = number
  default     = 0.001
}
