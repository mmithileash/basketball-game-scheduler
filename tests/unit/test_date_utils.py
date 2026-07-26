from datetime import date, datetime, timedelta, timezone

import pytest

from common.date_utils import sfn_timestamps_for_game, week_start_for_date


def _dt(iso: str) -> datetime:
    """Parse an ISO timestamp string produced by sfn_timestamps_for_game."""
    return datetime.fromisoformat(iso)


def _now(y, m, d, h=10, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def _fixed_policy(start="10:00 AM", duration=2):
    tier = {"startTime": start, "durationHours": duration}
    return {"minPlayers": 6, "threshold": 10, "longGame": dict(tier), "shortGame": dict(tier)}


def _two_tier_policy(long_start="10:00 AM", long_dur=2, short_start="11:00 AM", short_dur=1):
    return {
        "minPlayers": 6,
        "threshold": 10,
        "longGame": {"startTime": long_start, "durationHours": long_dur},
        "shortGame": {"startTime": short_start, "durationHours": short_dur},
    }


@pytest.mark.unit
def test_week_start_returns_monday_of_same_week():
    assert week_start_for_date(date(2026, 6, 24)) == date(2026, 6, 22)  # Wednesday → Monday


@pytest.mark.unit
def test_week_start_from_monday_returns_self():
    assert week_start_for_date(date(2026, 6, 22)) == date(2026, 6, 22)


@pytest.mark.unit
def test_week_start_from_sunday_returns_previous_monday():
    assert week_start_for_date(date(2026, 6, 28)) == date(2026, 6, 22)


# ---------------------------------------------------------------------------
# Adaptive lifecycle timestamps — worked examples from the design handoff.
# Game Sat 2026-08-01, 10:00, 2h (fixed). All times UTC.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_timestamps_at_48h_floor():
    """Thu reply, L=48h, t=0: announce at creation, offsets at the compressed floor."""
    ts = sfn_timestamps_for_game("2026-08-01", _fixed_policy(), _now(2026, 7, 30))
    assert ts["announce_at"] == "2026-07-30T10:00:00+00:00"  # start - 48h
    assert ts["reminder_at"] == "2026-07-30T22:00:00+00:00"  # start - 36h
    assert ts["confirm_at"] == "2026-07-31T10:00:00+00:00"   # start - 24h
    assert ts["finalize_at"] == "2026-08-01T12:00:00+00:00"  # start + 2h


@pytest.mark.unit
def test_timestamps_intermediate_lead_floors_confirm_down():
    """Wed reply, L=72h, t=0.2: confirm lands at 05:12 and floors down to 05:00."""
    ts = sfn_timestamps_for_game("2026-08-01", _fixed_policy(), _now(2026, 7, 29))
    assert ts["announce_at"] == "2026-07-29T10:00:00+00:00"
    assert ts["confirm_at"] == "2026-07-31T05:00:00+00:00"   # 05:12 floored to the hour
    assert ts["finalize_at"] == "2026-08-01T12:00:00+00:00"


@pytest.mark.unit
def test_timestamps_far_out_game_caps_at_seven_days():
    """Future-week game, t=1: announce caps at 7d, reminder 4d, confirm 2d."""
    ts = sfn_timestamps_for_game("2026-08-15", _fixed_policy(), _now(2026, 7, 27))
    assert ts["announce_at"] == "2026-08-08T10:00:00+00:00"  # start - 7d
    assert ts["reminder_at"] == "2026-08-11T10:00:00+00:00"  # start - 4d
    assert ts["confirm_at"] == "2026-08-13T10:00:00+00:00"   # start - 2d
    assert ts["finalize_at"] == "2026-08-15T12:00:00+00:00"


@pytest.mark.unit
def test_game_date_passed_through():
    ts = sfn_timestamps_for_game("2026-08-01", _fixed_policy(), _now(2026, 7, 30))
    assert ts["game_date"] == "2026-08-01"


@pytest.mark.unit
@pytest.mark.parametrize("now", [_now(2026, 7, 30), _now(2026, 7, 29), _now(2026, 7, 27, 12)])
def test_offsets_always_correctly_ordered(now):
    """announce < reminder < confirm < start for any lead >= 48h."""
    ts = sfn_timestamps_for_game("2026-08-01", _fixed_policy(), now)
    start = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    a, r, c = _dt(ts["announce_at"]), _dt(ts["reminder_at"]), _dt(ts["confirm_at"])
    assert a < r < c < start


@pytest.mark.unit
@pytest.mark.parametrize("now", [_now(2026, 7, 30), _now(2026, 7, 29), _now(2026, 7, 27, 12)])
def test_rsvp_window_and_confirm_notice_at_least_24h(now):
    """announce->confirm (RSVP window) and confirm->start (notice) are each >= 24h."""
    ts = sfn_timestamps_for_game("2026-08-01", _fixed_policy(), now)
    start = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    a, c = _dt(ts["announce_at"]), _dt(ts["confirm_at"])
    assert c - a >= timedelta(hours=24)
    assert start - c >= timedelta(hours=24)


@pytest.mark.unit
def test_confirm_never_later_than_real_freeze():
    """Flooring only moves the cutoff earlier — never past the true confirm moment."""
    now = _now(2026, 7, 28)  # Tue reply → confirm 00:24 pre-floor
    ts = sfn_timestamps_for_game("2026-08-01", _fixed_policy(), now)
    assert ts["confirm_at"] == "2026-07-31T00:00:00+00:00"  # 00:24 floored down


@pytest.mark.unit
def test_finalize_uses_maximum_end_across_tiers():
    """Finalize is the later of the two tiers' ends, so it is never before the real end."""
    # long: 10:00 +2h = 12:00 ; short: 11:00 +2h = 13:00 → max is 13:00
    policy = _two_tier_policy(long_start="10:00 AM", long_dur=2, short_start="11:00 AM", short_dur=2)
    ts = sfn_timestamps_for_game("2026-08-01", policy, _now(2026, 7, 27))
    assert ts["finalize_at"] == "2026-08-01T13:00:00+00:00"


@pytest.mark.unit
def test_finalize_rolls_date_for_past_midnight_game():
    """An evening game running past midnight finalizes on the following day."""
    policy = _fixed_policy(start="10:00 PM", duration=3)  # 22:00 + 3h = 01:00 next day
    ts = sfn_timestamps_for_game("2026-08-01", policy, _now(2026, 7, 27))
    assert ts["finalize_at"] == "2026-08-02T01:00:00+00:00"


@pytest.mark.unit
def test_pre_confirm_timestamps_anchor_to_earliest_tier_start():
    """The lifecycle anchors to min(long, short) start so the confirm-notice holds for either tier."""
    # short tier kicks off earlier (09:00) than long (10:00): anchor is 09:00.
    policy = _two_tier_policy(long_start="10:00 AM", short_start="9:00 AM")
    ts = sfn_timestamps_for_game("2026-08-01", policy, _now(2026, 7, 30, 9))  # L=48h from 09:00
    assert ts["announce_at"] == "2026-07-30T09:00:00+00:00"  # earliest-start - 48h
    assert ts["confirm_at"] == "2026-07-31T09:00:00+00:00"   # earliest-start - 24h
