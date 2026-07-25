from datetime import date, datetime, time, timedelta, timezone


def week_start_for_date(d: date) -> date:
    """Return the Monday that starts the ISO week containing d."""
    return d - timedelta(days=d.weekday())


# Display formats a start time may arrive in, from config defaults ("10:00 AM")
# or the natural-language admin parser ("9:00 AM", drifting to "7pm" / "19:00").
# Order matters only in that the first match wins; all are unambiguous.
_START_TIME_FORMATS = (
    "%I:%M %p",  # 10:00 AM
    "%I:%M%p",   # 10:00AM
    "%I %p",     # 7 PM
    "%I%p",      # 7PM
    "%H:%M",     # 19:00
)


def parse_start_time(value: str) -> time:
    """Parse a display start-time string to a precise `datetime.time`.

    Accepts 12-hour (with AM/PM) and 24-hour display strings, tolerating
    surrounding whitespace and mixed casing. The value is never truncated to a
    whole hour — minutes are preserved. Raises ``ValueError`` when the string
    matches none of the accepted formats, so an unparseable admin-supplied time
    can hold the batch and a misconfigured config default fails loudly.
    """
    if not isinstance(value, str):
        raise ValueError(f"start time must be a string, got {type(value).__name__}")
    normalised = value.strip().upper()
    if not normalised:
        raise ValueError("start time is empty")
    for fmt in _START_TIME_FORMATS:
        try:
            return datetime.strptime(normalised, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"could not parse start time: {value!r}")


# Adaptive lifecycle offsets. Each of announce/reminder/confirm slides on a
# single shared parameter t driven by the game's lead time L:
#   t = clamp((L - 48h) / (7d - 48h), 0, 1)   # 0 at the 48h floor, 1 at 7d+
# Announce shares this exact window by design — its floor IS the 48h lead floor
# and its cap IS the 7d lead cap — so it collapses to "at creation" for any game
# under a week out. Reminder and confirm slide over their own narrower ranges.
_FLOOR_LEAD = timedelta(hours=48)
_IDEAL_LEAD = timedelta(days=7)
_LEAD_SPAN = _IDEAL_LEAD - _FLOOR_LEAD                 # 120h → announce 48h..7d
_REMINDER_FLOOR = timedelta(hours=36)
_REMINDER_SPAN = timedelta(days=4) - _REMINDER_FLOOR   # 60h → 36h..4d
_CONFIRM_FLOOR = timedelta(hours=24)
_CONFIRM_SPAN = timedelta(days=2) - _CONFIRM_FLOOR     # 24h → 24h..2d


def game_start(game_date: str, policy: dict) -> datetime:
    """The game's anchor start: its date combined with the earlier tier start.

    Because the tier isn't resolved until confirmation, anchoring the pre-confirm
    lifecycle (and the viability guard) to the earliest possible kickoff
    guarantees the confirm-notice invariant holds for whichever tier confirms.
    For a fixed game both tiers are equal, so the minimum is trivial. Shared by
    the guard and the lifecycle so a game that passes the guard is guaranteed a
    coherent lifecycle.
    """
    d = date.fromisoformat(game_date)
    long_start = parse_start_time(policy["longGame"]["startTime"])
    short_start = parse_start_time(policy["shortGame"]["startTime"])
    earliest = min(long_start, short_start)
    return datetime.combine(d, earliest, tzinfo=timezone.utc)


def _finalize_at(game_date: str, policy: dict) -> datetime:
    """Static, conservative game end: date + the maximum end across both tiers.

    Each tier's end is its start plus its duration, computed with a timedelta so
    an evening game running past midnight rolls the date correctly. The maximum
    is always at or after the real end regardless of which tier confirms.
    """
    d = date.fromisoformat(game_date)
    ends = []
    for tier in (policy["longGame"], policy["shortGame"]):
        start_dt = datetime.combine(d, parse_start_time(tier["startTime"]), tzinfo=timezone.utc)
        ends.append(start_dt + timedelta(hours=int(tier["durationHours"])))
    return max(ends)


def sfn_timestamps_for_game(game_date: str, policy: dict, now: datetime) -> dict:
    """Return the four ISO-8601 UTC timestamps for a game's SFN execution input.

    The announce/reminder/confirm moments are computed relative to the game's
    real (earliest-tier) start, scaling smoothly between the generous 7d/4d/2d
    spacing for far-out games and a compressed 48h/36h/24h floor for same-week
    games. `confirm_at` is floored down to the top of the hour so the deadline
    shown to players is never later than the real roster freeze. `finalize_at`
    is the conservative maximum end across both tiers. `now` is supplied (not
    read from the clock) so the guard and the lifecycle share one instant and
    the timing is exhaustively testable.
    """
    start = game_start(game_date, policy)
    lead = start - now
    t = (lead - _FLOOR_LEAD) / _LEAD_SPAN
    t = max(0.0, min(1.0, t))

    # announce/reminder aren't deadlines, but float interpolation can leave
    # sub-second noise in the ISO timestamps; trim to whole seconds for clean
    # SFN input. confirm is the shown deadline, floored down to the whole hour.
    announce_at = (start - (_FLOOR_LEAD + t * _LEAD_SPAN)).replace(microsecond=0)
    reminder_at = (start - (_REMINDER_FLOOR + t * _REMINDER_SPAN)).replace(microsecond=0)
    confirm_at = start - (_CONFIRM_FLOOR + t * _CONFIRM_SPAN)
    confirm_at = confirm_at.replace(minute=0, second=0, microsecond=0)

    return {
        "game_date": game_date,
        "announce_at": announce_at.isoformat(),
        "reminder_at": reminder_at.isoformat(),
        "confirm_at": confirm_at.isoformat(),
        "finalize_at": _finalize_at(game_date, policy).isoformat(),
    }
