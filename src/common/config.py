import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    players_table: str
    games_table: str
    email_bucket: str
    sender_email: str
    admin_email: str
    game_time: str
    game_location: str
    bedrock_model_id: str
    min_players: int


def load_config() -> Config:
    return Config(
        players_table=os.environ["PLAYERS_TABLE"],
        games_table=os.environ["GAMES_TABLE"],
        email_bucket=os.environ["EMAIL_BUCKET"],
        sender_email=os.environ["SENDER_EMAIL"],
        admin_email=os.environ["ADMIN_EMAIL"],
        game_time=os.environ.get("GAME_TIME", "10:00 AM"),
        game_location=os.environ.get("GAME_LOCATION", "TBD"),
        bedrock_model_id=os.environ.get(
            "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
        ),
        min_players=int(os.environ.get("MIN_PLAYERS", "6")),
    )
