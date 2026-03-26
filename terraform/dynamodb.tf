resource "aws_dynamodb_table" "players" {
  name         = "Players"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "email"
  range_key    = "active"

  attribute {
    name = "email"
    type = "S"
  }

  attribute {
    name = "active"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Name = "Players"
  }
}

resource "aws_dynamodb_table" "games" {
  name         = "Games"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "gameDate"
  range_key    = "sk"

  attribute {
    name = "gameDate"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Name = "Games"
  }
}
