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
