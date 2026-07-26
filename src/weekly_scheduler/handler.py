import logging
from datetime import date
from typing import Any

from common.config import load_config
from common.date_utils import week_start_for_date
from common.dynamo import count_games_in_week, get_active_admins, get_week_status
from common.email_service import send_admin_weekly_prompt

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: prompt admins to schedule games for the current week (MON 9AM UTC).

    The prompt targets the week containing today so games can be arranged and
    played in the same week. It is suppressed when the week already has at least
    the floor number of live games, or when a no-game decision has been recorded.
    """
    config = load_config()
    today = date.today()
    week_start_str = week_start_for_date(today).isoformat()

    live_count = count_games_in_week(week_start_str)
    if live_count >= config.min_games_per_week:
        logger.info(f"Week {week_start_str} already has {live_count} game(s), no prompt needed")
        return {
            "statusCode": 200,
            "body": {"action": "no_prompt", "weekStart": week_start_str, "gameCount": live_count},
        }

    week_status = get_week_status(week_start_str)
    if week_status and week_status.get("reason"):
        logger.info(f"Week {week_start_str} has a no-game decision recorded, no prompt needed")
        return {
            "statusCode": 200,
            "body": {"action": "no_prompt", "weekStart": week_start_str, "reason": "declined"},
        }

    admins = get_active_admins()
    for admin in admins:
        try:
            send_admin_weekly_prompt(admin["email"], week_start_str)
        except Exception:
            logger.error(f"Failed to send weekly prompt to {admin['email']}", exc_info=True)

    logger.info(f"Sent weekly scheduling prompt for {week_start_str} to {len(admins)} admin(s)")
    return {
        "statusCode": 200,
        "body": {"action": "prompt_sent", "weekStart": week_start_str, "adminCount": len(admins)},
    }
