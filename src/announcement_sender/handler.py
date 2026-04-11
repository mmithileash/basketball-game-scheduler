import logging
from datetime import date, timedelta
from typing import Any

from common.dynamo import create_game, get_active_players, get_game_status
from common.email_service import send_announcement, send_no_game_announcement

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _next_saturday() -> str:
    """Calculate the date of the coming Saturday (assumes today is Monday)."""
    today = date.today()
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    saturday = today + timedelta(days=days_until_saturday)
    return saturday.isoformat()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: announce a new game for next Saturday."""
    game_date = _next_saturday()
    logger.info(f"Checking game status for {game_date}")

    existing = get_game_status(game_date)
    if existing and existing.get("status") == "CANCELLED":
        logger.info(f"Game {game_date} is pre-cancelled — sending no-game notification")
        players = get_active_players()
        sent_count = 0
        for player in players:
            try:
                send_no_game_announcement(player["email"], player.get("name"), game_date)
                sent_count += 1
            except Exception:
                logger.error(f"Failed to send no-game notification to {player['email']}", exc_info=True)

        logger.info(f"Sent {sent_count}/{len(players)} no-game notifications for {game_date}")
        return {
            "statusCode": 200,
            "body": {
                "action": "pre_cancelled",
                "gameDate": game_date,
                "notifiedCount": sent_count,
            },
        }

    logger.info(f"Creating game for {game_date}")
    create_game(game_date)

    players = get_active_players()
    logger.info(f"Sending announcements to {len(players)} players")

    sent_count = 0
    for player in players:
        try:
            send_announcement(player["email"], player.get("name"), game_date)
            sent_count += 1
        except Exception:
            logger.error(f"Failed to send announcement to {player['email']}", exc_info=True)

    logger.info(f"Sent {sent_count}/{len(players)} announcements for game {game_date}")

    return {
        "statusCode": 200,
        "body": {
            "gameDate": game_date,
            "playerCount": len(players),
            "sentCount": sent_count,
        },
    }
