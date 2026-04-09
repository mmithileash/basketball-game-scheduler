import logging
from datetime import date
from typing import Any

from common.dynamo import (
    get_current_open_game,
    get_pending_players,
    get_roster,
    update_game_status,
)
from common.config import load_config
from common.email_service import send_cancellation, send_confirmation, send_reminder

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _guest_contact_emails(roster: dict[str, Any], statuses: list[str]) -> list[str]:
    """Return contact emails for guests who have their own email (sk='guest#active')."""
    return [
        guest["pk"]
        for status in statuses
        for guest in roster.get(status, {}).get("guests", [])
        if guest.get("sk") == "guest#active"
    ]


def _count_confirmed(roster: dict[str, Any]) -> int:
    """Count confirmed players including their guests."""
    yes = roster.get("YES", {})
    return len(yes.get("players", {})) + len(yes.get("guests", []))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: send reminders or cancellations based on game status."""
    config = load_config()
    MIN_PLAYERS = config.min_players
    open_game = get_current_open_game()
    if not open_game:
        logger.info("No open game found, nothing to do")
        return {"statusCode": 200, "body": "No open game"}

    game_date = open_game["gameDate"]
    roster = get_roster(game_date)
    confirmed_count = _count_confirmed(roster)

    today = date.today()
    day_of_week = today.weekday()  # 0=Monday, 2=Wednesday, 4=Friday

    logger.info(
        "Checking game %s: %d confirmed, day_of_week=%d",
        game_date, confirmed_count, day_of_week,
    )

    if day_of_week == 2:  # Wednesday
        if confirmed_count < MIN_PLAYERS:
            pending = get_pending_players(game_date)
            logger.info("Wednesday reminder: sending to %d pending players", len(pending))
            for player in pending:
                try:
                    send_reminder(
                        player["email"],
                        player.get("name"),
                        confirmed_count,
                        game_date,
                    )
                except Exception:
                    logger.error(
                        "Failed to send reminder to %s", player["email"],
                        exc_info=True,
                    )
            return {
                "statusCode": 200,
                "body": {
                    "action": "reminders_sent",
                    "gameDate": game_date,
                    "confirmedCount": confirmed_count,
                    "remindersSent": len(pending),
                },
            }

    elif day_of_week == 4:  # Friday
        if confirmed_count < MIN_PLAYERS:
            # Cancel the game
            logger.info("Friday: cancelling game %s (%d confirmed)", game_date, confirmed_count)
            update_game_status(game_date, "CANCELLED")

            # Notify all players (confirmed + pending)
            all_emails: set[str] = set()
            for status_data in roster.values():
                all_emails.update(status_data["players"].keys())
            pending = get_pending_players(game_date)
            for player in pending:
                all_emails.add(player["email"])

            for player_email in all_emails:
                try:
                    send_cancellation(player_email, game_date)
                except Exception:
                    logger.error(
                        "Failed to send cancellation to %s", player_email,
                        exc_info=True,
                    )

            for guest_email in _guest_contact_emails(roster, ["YES", "MAYBE"]):
                try:
                    send_cancellation(guest_email, game_date)
                except Exception:
                    logger.error(
                        "Failed to send cancellation to guest %s", guest_email,
                        exc_info=True,
                    )

            return {
                "statusCode": 200,
                "body": {
                    "action": "game_cancelled",
                    "gameDate": game_date,
                    "confirmedCount": confirmed_count,
                },
            }
        else:
            # Game is confirmed - send confirmation to YES players
            logger.info("Friday: confirming game %s (%d confirmed)", game_date, confirmed_count)
            yes_players = roster.get("YES", {}).get("players", {})
            for player_email in yes_players:
                try:
                    send_confirmation(player_email, game_date, roster)
                except Exception:
                    logger.error(
                        "Failed to send confirmation to %s", player_email,
                        exc_info=True,
                    )

            for guest_email in _guest_contact_emails(roster, ["YES"]):
                try:
                    send_confirmation(guest_email, game_date, roster)
                except Exception:
                    logger.error(
                        "Failed to send confirmation to guest %s", guest_email,
                        exc_info=True,
                    )

            return {
                "statusCode": 200,
                "body": {
                    "action": "game_confirmed",
                    "gameDate": game_date,
                    "confirmedCount": confirmed_count,
                },
            }

    logger.info("No action needed today (day_of_week=%d)", day_of_week)
    return {
        "statusCode": 200,
        "body": {
            "action": "no_action",
            "gameDate": game_date,
            "confirmedCount": confirmed_count,
        },
    }
