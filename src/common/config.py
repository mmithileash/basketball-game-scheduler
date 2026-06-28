import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    players_table: str
    games_table: str
    email_bucket: str
    sender_email: str
    admin_email: str
    game_location: str
    game_map_url: str
    bedrock_model_id: str
    min_players: int
    long_game_threshold: int
    long_game_start_time: str
    long_game_duration_hours: int
    short_game_start_time: str
    short_game_duration_hours: int
    max_games_per_week: int


def load_config() -> Config:
    return Config(
        players_table=os.environ["PLAYERS_TABLE"],
        games_table=os.environ["GAMES_TABLE"],
        email_bucket=os.environ["EMAIL_BUCKET"],
        sender_email=os.environ["SENDER_EMAIL"],
        admin_email=os.environ["ADMIN_EMAIL"],
        game_location=os.environ.get("GAME_LOCATION", "TBD"),
        game_map_url=os.environ.get("GAME_MAP_URL", ""),
        bedrock_model_id=os.environ.get(
            "BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        ),
        min_players=int(os.environ.get("MIN_PLAYERS", "6")),
        long_game_threshold=int(os.environ.get("LONG_GAME_THRESHOLD", "10")),
        long_game_start_time=os.environ.get("LONG_GAME_START_TIME", "10:00 AM"),
        long_game_duration_hours=int(os.environ.get("LONG_GAME_DURATION_HOURS", "2")),
        short_game_start_time=os.environ.get("SHORT_GAME_START_TIME", "11:00 AM"),
        short_game_duration_hours=int(os.environ.get("SHORT_GAME_DURATION_HOURS", "1")),
        max_games_per_week=int(os.environ.get("MAX_GAMES_PER_WEEK", "1")),
    )
