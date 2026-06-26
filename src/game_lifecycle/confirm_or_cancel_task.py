import logging
from typing import Any

from common.config import load_config
from common.dynamo import get_game_status, get_pending_players, get_roster, update_game_status
from common.email_service import send_cancellation, send_final_confirmation_with_duration

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _count_confirmed(roster: dict[str, Any]) -> int:
    yes = roster.get("YES", {})
    return len(yes.get("players", {})) + len(yes.get("guests", []))


def _guest_contact_emails(roster: dict[str, Any], statuses: list[str]) -> list[str]:
    return [
        guest["pk"]
        for status in statuses
        for guest in roster.get(status, {}).get("guests", [])
        if guest.get("sk") == "guest#active"
    ]


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SFN task: confirm or cancel the game 2 days before."""
    game_date: str = event["game_date"]
    config = load_config()

    game = get_game_status(game_date)
    if not game or game.get("status") != "OPEN":
        logger.info(f"Game {game_date} not OPEN, skipping confirm/cancel")
        return {"game_date": game_date, "game_open": False}

    roster = get_roster(game_date)
    confirmed_count = _count_confirmed(roster)

    if confirmed_count < config.min_players:
        update_game_status(game_date, "CANCELLED")

        all_emails: set[str] = set()
        for status_data in roster.values():
            all_emails.update(status_data.get("players", {}).keys())
        for player in get_pending_players(game_date):
            all_emails.add(player["email"])

        for email in all_emails:
            try:
                send_cancellation(email, game_date)
            except Exception:
                logger.error(f"Failed to send cancellation to {email}", exc_info=True)

        for guest_email in _guest_contact_emails(roster, ["YES", "MAYBE"]):
            try:
                send_cancellation(guest_email, game_date)
            except Exception:
                logger.error(f"Failed to send cancellation to guest {guest_email}", exc_info=True)

        logger.info(f"Cancelled game {game_date}: {confirmed_count} confirmed < {config.min_players}")
        return {"game_date": game_date, "game_open": False}

    duration_hours = 2 if confirmed_count >= config.long_game_threshold else 1

    yes_players = roster.get("YES", {}).get("players", {})
    for player_email in yes_players:
        try:
            send_final_confirmation_with_duration(player_email, game_date, roster, duration_hours)
        except Exception:
            logger.error(f"Failed to send confirmation to {player_email}", exc_info=True)

    for guest_email in _guest_contact_emails(roster, ["YES"]):
        try:
            send_final_confirmation_with_duration(guest_email, game_date, roster, duration_hours)
        except Exception:
            logger.error(f"Failed to send confirmation to guest {guest_email}", exc_info=True)

    logger.info(f"Confirmed game {game_date}: {confirmed_count} confirmed, {duration_hours}h duration")
    return {"game_date": game_date, "game_open": True}
