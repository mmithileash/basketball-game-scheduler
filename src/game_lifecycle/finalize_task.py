import logging
from typing import Any

from common.dynamo import delete_guest_entries, get_game_status, get_roster, update_game_status

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SFN task: mark game PLAYED and clean up guest entries."""
    game_date: str = event["game_date"]

    game = get_game_status(game_date)
    if not game or game.get("status") != "OPEN":
        logger.info(f"Game {game_date} not OPEN (status={game.get('status') if game else 'missing'}), skipping finalize")
        return {"game_date": game_date, "action": "no_op"}

    update_game_status(game_date, "PLAYED")

    all_guests: list = []
    try:
        roster = get_roster(game_date)
        all_guests = (
            roster["YES"]["guests"]
            + roster["NO"]["guests"]
            + roster["MAYBE"]["guests"]
        )
        if all_guests:
            delete_guest_entries(all_guests)
    except Exception:
        logger.exception(f"Failed to delete guest entries for {game_date}")

    logger.info(f"Finalized game {game_date}: marked PLAYED, deleted {len(all_guests)} guest entry/entries")
    return {"game_date": game_date, "action": "marked_played", "guests_deleted": len(all_guests)}
