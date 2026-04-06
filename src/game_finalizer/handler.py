import logging
from datetime import date
from typing import Any

from common.dynamo import delete_guest_entries, get_game_status, get_roster, update_game_status

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: mark today's game as PLAYED and clean up guest entries."""
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

        all_guests: list = []
        try:
            roster = get_roster(game_date)
            all_guests = (
                roster["YES"]["guests"]
                + roster["NO"]["guests"]
                + roster["MAYBE"]["guests"]
            )
            if all_guests:
                delete_guest_entries(all_guests)
                logger.info(f"Deleted {len(all_guests)} guest Players entries for {game_date}")
        except Exception:
            logger.exception(f"Failed to delete guest entries for {game_date}, manual cleanup may be required")

        return {
            "statusCode": 200,
            "body": {
                "action": "game_marked_played",
                "gameDate": game_date,
                "guestsDeleted": len(all_guests),
            },
        }

    logger.info(f"Game {game_date} already has status {status}, no-op")
    return {
        "statusCode": 200,
        "body": {"action": "no_action", "gameDate": game_date, "status": status},
    }
