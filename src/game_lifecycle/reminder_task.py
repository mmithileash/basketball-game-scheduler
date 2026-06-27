import logging
from typing import Any

from common.dynamo import get_game_status, get_pending_players, get_roster
from common.email_service import send_reminder

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _count_confirmed(roster: dict[str, Any]) -> int:
    yes = roster.get("YES", {})
    return len(yes.get("players", {})) + len(yes.get("guests", []))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SFN task: send low-signup reminder 4 days before the game."""
    game_date: str = event["game_date"]

    game = get_game_status(game_date)
    if not game or game.get("status") != "OPEN":
        logger.info(f"Game {game_date} not OPEN, skipping reminder")
        return {"game_date": game_date, "game_open": False}

    policy = game["policy"]
    min_players = int(policy["minPlayers"])
    roster = get_roster(game_date)
    confirmed_count = _count_confirmed(roster)

    if confirmed_count < min_players:
        pending = get_pending_players(game_date)
        for player in pending:
            try:
                send_reminder(player["email"], player.get("name"), confirmed_count, game_date, min_players)
            except Exception:
                logger.error(f"Failed to send reminder to {player['email']}", exc_info=True)
        logger.info(f"Sent reminders for {game_date}: {confirmed_count} confirmed, {len(pending)} pending")
    else:
        logger.info(f"Game {game_date}: {confirmed_count} confirmed, no reminder needed")

    return {"game_date": game_date, "game_open": True}
