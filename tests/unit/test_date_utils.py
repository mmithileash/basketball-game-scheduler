from datetime import date

import pytest

from common.date_utils import next_saturday


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
