from datetime import date, timezone

import pytest

from common.date_utils import next_saturday, sfn_timestamps_for_game, week_start_for_date


@pytest.mark.unit
def test_next_saturday_from_monday():
    assert next_saturday(date(2026, 3, 23)) == date(2026, 3, 28)


@pytest.mark.unit
def test_next_saturday_from_wednesday():
    assert next_saturday(date(2026, 3, 25)) == date(2026, 3, 28)


@pytest.mark.unit
def test_next_saturday_from_friday():
    assert next_saturday(date(2026, 3, 27)) == date(2026, 3, 28)


@pytest.mark.unit
def test_next_saturday_from_saturday():
    # Saturday itself returns today (game day)
    assert next_saturday(date(2026, 3, 28)) == date(2026, 3, 28)


@pytest.mark.unit
def test_next_saturday_from_sunday():
    assert next_saturday(date(2026, 3, 29)) == date(2026, 4, 4)


@pytest.mark.unit
def test_next_saturday_always_returns_saturday():
    result = next_saturday()
    assert result.weekday() == 5


@pytest.mark.unit
def test_next_saturday_within_seven_days():
    result = next_saturday()
    today = date.today()
    assert today <= result <= today + __import__("datetime").timedelta(days=7)


@pytest.mark.unit
def test_next_saturday_default_uses_today(mocker):
    fake_today = date(2026, 4, 8)  # Wednesday
    mocker.patch("common.date_utils.date").today.return_value = fake_today
    assert next_saturday() == date(2026, 4, 11)


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
