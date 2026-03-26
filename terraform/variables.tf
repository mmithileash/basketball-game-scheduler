variable "domain_name" {
  description = "Domain name for SES email and Route 53 hosted zone (e.g. hoops.example.com)"
  type        = string
}

variable "sender_email" {
  description = "Email address used to send game announcements and reminders (e.g. scheduler@hoops.example.com)"
  type        = string
}

variable "game_time" {
  description = "Default game time displayed in announcements"
  type        = string
  default     = "10:00 AM"
}

variable "game_location" {
  description = "Default game location displayed in announcements"
  type        = string
  default     = "TBD"
}

variable "bedrock_model_id" {
  description = "AWS Bedrock model ID for NLU intent parsing"
  type        = string
  default     = "anthropic.claude-3-haiku-20240307-v1:0"
}

variable "min_players" {
  description = "Minimum number of confirmed players required for a game to proceed"
  type        = number
  default     = 6
}

variable "environment" {
  description = "Environment tag (e.g. dev, staging, prod)"
  type        = string
  default     = "prod"
}
