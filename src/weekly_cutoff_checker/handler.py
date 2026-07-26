import logging
from datetime import date
from typing import Any

from common.date_utils import week_start_for_date
from common.dynamo import (
    count_games_in_week,
    get_active_players,
    get_week_status,
    set_week_no_game,
)
from common.email_service import send_no_game_this_week

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: Tuesday 9PM UTC cutoff for the current week.

    Sends "no game this week" only when the week genuinely ends with zero games
    and no prior no-game decision was recorded. A recorded decision keeps the
    cutoff idempotent; a week with live games self-announces, so no broadcast is
    sent.
    """
    today = date.today()
    week_start_str = week_start_for_date(today).isoformat()

    week_status = get_week_status(week_start_str)
    if week_status and week_status.get("reason"):
        logger.info(f"Week {week_start_str} already has a no-game decision, no cutoff action needed")
        return {
            "statusCode": 200,
            "body": {"action": "already_handled", "weekStart": week_start_str},
        }

    if count_games_in_week(week_start_str) > 0:
        logger.info(f"Week {week_start_str} has games scheduled, no 'no game' broadcast")
        return {
            "statusCode": 200,
            "body": {"action": "games_scheduled", "weekStart": week_start_str},
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

    logger.info(f"Cutoff: no games for {week_start_str}, notified {len(players)} player(s)")
    return {
        "statusCode": 200,
        "body": {"action": "no_game_sent", "weekStart": week_start_str, "notifiedCount": len(players)},
    }
