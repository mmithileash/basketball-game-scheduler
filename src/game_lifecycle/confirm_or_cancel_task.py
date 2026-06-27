import logging
from typing import Any

from common.dynamo import (
    freeze_game_schedule,
    get_game_status,
    get_pending_players,
    get_roster,
    update_game_status,
)
from common.email_service import send_cancellation, send_final_confirmation_with_duration
from common.policy import resolve_tier

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

    game = get_game_status(game_date)
    if not game or game.get("status") != "OPEN":
        logger.info(f"Game {game_date} not OPEN, skipping confirm/cancel")
        return {"game_date": game_date, "game_open": False}

    policy = game["policy"]
    min_players = int(policy["minPlayers"])
    roster = get_roster(game_date)
    confirmed_count = _count_confirmed(roster)

    if confirmed_count < min_players:
        update_game_status(game_date, "CANCELLED")

        all_emails: set[str] = set()
        for status_data in roster.values():
            all_emails.update(status_data.get("players", {}).keys())
        for player in get_pending_players(game_date):
            all_emails.add(player["email"])

        for email in all_emails:
            try:
                send_cancellation(email, game_date, min_players)
            except Exception:
                logger.error(f"Failed to send cancellation to {email}", exc_info=True)

        for guest_email in _guest_contact_emails(roster, ["YES", "MAYBE"]):
            try:
                send_cancellation(guest_email, game_date, min_players)
            except Exception:
                logger.error(f"Failed to send cancellation to guest {guest_email}", exc_info=True)

        logger.info(f"Cancelled game {game_date}: {confirmed_count} confirmed < {min_players}")
        return {"game_date": game_date, "game_open": False}

    # Resolve the tier from turnout and freeze the decision so it can't drift later.
    tier = resolve_tier(policy, confirmed_count)
    start_time = tier["startTime"]
    duration_hours = tier["durationHours"]
    freeze_game_schedule(game_date, start_time, duration_hours)

    yes_players = roster.get("YES", {}).get("players", {})
    for player_email in yes_players:
        try:
            send_final_confirmation_with_duration(player_email, game_date, roster, start_time, duration_hours)
        except Exception:
            logger.error(f"Failed to send confirmation to {player_email}", exc_info=True)

    for guest_email in _guest_contact_emails(roster, ["YES"]):
        try:
            send_final_confirmation_with_duration(guest_email, game_date, roster, start_time, duration_hours)
        except Exception:
            logger.error(f"Failed to send confirmation to guest {guest_email}", exc_info=True)

    logger.info(f"Confirmed game {game_date}: {confirmed_count} confirmed, {start_time} for {duration_hours}h")
    return {"game_date": game_date, "game_open": True}
