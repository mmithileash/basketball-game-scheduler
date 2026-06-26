import logging
from typing import Any

from common.config import load_config
from common.dynamo import get_active_players, get_game_status
from common.email_service import send_tentative_announcement

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SFN task: send announcement emails 7 days before the game."""
    game_date: str = event["game_date"]
    config = load_config()

    game = get_game_status(game_date)
    if not game or game.get("status") != "OPEN":
        logger.info(f"Game {game_date} not OPEN, skipping announcement")
        return {"game_date": game_date, "game_open": False}

    players = get_active_players()
    for player in players:
        try:
            send_tentative_announcement(
                player["email"],
                player.get("name"),
                game_date,
                config.long_game_threshold,
            )
        except Exception:
            logger.error(f"Failed to send announcement to {player['email']}", exc_info=True)

    logger.info(f"Announced game {game_date} to {len(players)} player(s)")
    return {"game_date": game_date, "game_open": True}
