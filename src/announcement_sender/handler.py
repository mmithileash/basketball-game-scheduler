import logging
from datetime import date, timedelta
from typing import Any

from common.dynamo import create_game, get_active_players
from common.email_service import send_announcement

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
    logger.info("Creating game for %s", game_date)

    create_game(game_date)

    players = get_active_players()
    logger.info("Sending announcements to %d players", len(players))

    sent_count = 0
    for player in players:
        try:
            send_announcement(player["email"], player.get("name"), game_date)
            sent_count += 1
        except Exception:
            logger.error(
                "Failed to send announcement to %s", player["email"],
                exc_info=True,
            )

    logger.info("Sent %d/%d announcements for game %s",
                sent_count, len(players), game_date)

    return {
        "statusCode": 200,
        "body": {
            "gameDate": game_date,
            "playerCount": len(players),
            "sentCount": sent_count,
        },
    }
