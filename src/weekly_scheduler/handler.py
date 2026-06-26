import logging
from datetime import date, timedelta
from typing import Any

from common.config import load_config
from common.date_utils import week_start_for_date
from common.dynamo import get_active_admins, get_week_status
from common.email_service import send_admin_weekly_prompt

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: prompt admins to schedule games for next week (MON 9AM UTC)."""
    config = load_config()
    today = date.today()
    next_week_start = week_start_for_date(today + timedelta(days=7))
    week_start_str = next_week_start.isoformat()

    week_status = get_week_status(week_start_str)
    game_count = int(week_status.get("gameCount", 0)) if week_status else 0

    if game_count >= config.max_games_per_week:
        logger.info(f"Week {week_start_str} already has {game_count} game(s), no prompt needed")
        return {
            "statusCode": 200,
            "body": {"action": "no_prompt", "weekStart": week_start_str, "gameCount": game_count},
        }

    if week_status and week_status.get("adminResponded"):
        logger.info(f"Admin already responded for week {week_start_str}, no prompt needed")
        return {
            "statusCode": 200,
            "body": {"action": "no_prompt", "weekStart": week_start_str, "reason": "already_responded"},
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
