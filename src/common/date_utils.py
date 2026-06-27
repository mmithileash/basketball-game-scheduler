from datetime import date, datetime, timedelta, timezone


def week_start_for_date(d: date) -> date:
    """Return the Monday that starts the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def sfn_timestamps_for_game(game_date: str) -> dict:
    """Return the four ISO-8601 UTC timestamps for a game's SFN execution input.

    announce_at  = game_date - 7 days at 09:00 UTC
    reminder_at  = game_date - 4 days at 09:00 UTC
    confirm_at   = game_date - 2 days at 09:00 UTC
    finalize_at  = game_date at 13:00 UTC
    """
    d = date.fromisoformat(game_date)

    def _ts(dt: date, hour: int) -> str:
        return datetime(dt.year, dt.month, dt.day, hour, 0, 0, tzinfo=timezone.utc).isoformat()

    return {
        "game_date": game_date,
        "announce_at": _ts(d - timedelta(days=7), 9),
        "reminder_at": _ts(d - timedelta(days=4), 9),
        "confirm_at": _ts(d - timedelta(days=2), 9),
        "finalize_at": _ts(d, 13),
    }
