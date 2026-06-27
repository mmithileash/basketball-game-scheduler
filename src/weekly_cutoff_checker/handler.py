import logging
from datetime import date, timedelta
from typing import Any

from common.date_utils import week_start_for_date
from common.dynamo import get_active_players, get_week_status, set_week_no_game
from common.email_service import send_no_game_this_week

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: Tuesday 9PM UTC cutoff — notify players if admin didn't respond."""
    today = date.today()
    next_week_start = week_start_for_date(today + timedelta(days=7))
    week_start_str = next_week_start.isoformat()

    week_status = get_week_status(week_start_str)

    if week_status and week_status.get("adminResponded"):
        logger.info(f"Admin already responded for week {week_start_str}, no cutoff action needed")
        return {
            "statusCode": 200,
            "body": {"action": "already_responded", "weekStart": week_start_str},
        }

    set_week_no_game(week_start_str, "no_response")

    players = get_active_players()
    for player in players:
        try:
            send_no_game_this_week(
                player["email"], player.get("name"), week_start_str, "no_response"
            )
        except Exception:
            logger.error(f"Failed to notify {player['email']}", exc_info=True)

    logger.info(f"Cutoff: no admin response for {week_start_str}, notified {len(players)} player(s)")
    return {
        "statusCode": 200,
        "body": {"action": "no_game_sent", "weekStart": week_start_str, "notifiedCount": len(players)},
    }
