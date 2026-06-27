from datetime import date, timezone

import pytest

from common.date_utils import sfn_timestamps_for_game, week_start_for_date


@pytest.mark.unit
def test_week_start_returns_monday_of_same_week():
    assert week_start_for_date(date(2026, 6, 24)) == date(2026, 6, 22)  # Wednesday → Monday


@pytest.mark.unit
def test_week_start_from_monday_returns_self():
    assert week_start_for_date(date(2026, 6, 22)) == date(2026, 6, 22)


@pytest.mark.unit
def test_week_start_from_sunday_returns_previous_monday():
    assert week_start_for_date(date(2026, 6, 28)) == date(2026, 6, 22)


@pytest.mark.unit
def test_sfn_timestamps_announce_is_7_days_before_game():
    ts = sfn_timestamps_for_game("2026-07-07")
    assert ts["announce_at"] == "2026-06-30T09:00:00+00:00"


@pytest.mark.unit
def test_sfn_timestamps_reminder_is_4_days_before_game():
    ts = sfn_timestamps_for_game("2026-07-07")
    assert ts["reminder_at"] == "2026-07-03T09:00:00+00:00"


@pytest.mark.unit
def test_sfn_timestamps_confirm_is_2_days_before_game():
    ts = sfn_timestamps_for_game("2026-07-07")
    assert ts["confirm_at"] == "2026-07-05T09:00:00+00:00"


@pytest.mark.unit
def test_sfn_timestamps_finalize_is_1pm_on_game_day():
    ts = sfn_timestamps_for_game("2026-07-07")
    assert ts["finalize_at"] == "2026-07-07T13:00:00+00:00"


@pytest.mark.unit
def test_sfn_timestamps_includes_game_date():
    ts = sfn_timestamps_for_game("2026-07-07")
    assert ts["game_date"] == "2026-07-07"
