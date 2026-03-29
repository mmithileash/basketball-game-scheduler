import logging
from datetime import date
from typing import Any

from common.config import load_config
from common.dynamo import get_game_status, update_game_status

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

config = load_config()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: mark today's game as PLAYED if still OPEN."""
    game_date = date.today().isoformat()
    logger.info(f"game_finalizer running for {game_date}")

    game = get_game_status(game_date)

    if game is None:
        logger.info(f"No game found for {game_date}, nothing to do")
        return {"statusCode": 200, "body": "No game found"}

    status = game.get("status")

    if status == "OPEN":
        update_game_status(game_date, "PLAYED")
        logger.info(f"Marked game {game_date} as PLAYED")
        return {
            "statusCode": 200,
            "body": {"action": "game_marked_played", "gameDate": game_date},
        }

    logger.info(f"Game {game_date} already has status {status}, no-op")
    return {
        "statusCode": 200,
        "body": {"action": "no_action", "gameDate": game_date, "status": status},
    }
