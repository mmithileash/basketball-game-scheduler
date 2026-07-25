from datetime import date

import pytest

from common.date_utils import week_start_for_date
from weekly_scheduler.handler import handler


def _make_event():
    return {}


def _current_week():
    return week_start_for_date(date.today()).isoformat()


@pytest.mark.unit
def test_prompt_sent_when_week_below_floor(mocker):
    """When the week has fewer games than the floor and no decline, prompt all admins."""
    mocker.patch("weekly_scheduler.handler.count_games_in_week", return_value=0)
    mocker.patch("weekly_scheduler.handler.get_week_status", return_value=None)
    mocker.patch(
        "weekly_scheduler.handler.get_active_admins",
        return_value=[{"email": "admin@example.com", "name": "Admin"}],
    )
    mock_prompt = mocker.patch("weekly_scheduler.handler.send_admin_weekly_prompt")

    result = handler(_make_event(), None)

    assert result["statusCode"] == 200
    assert result["body"]["action"] == "prompt_sent"
    mock_prompt.assert_called_once()
    _, week_start = mock_prompt.call_args[0]
    assert week_start == _current_week()  # targets the CURRENT week, not next


@pytest.mark.unit
def test_prompt_targets_current_week(mocker):
    """The prompted week is the week containing today."""
    mocker.patch("weekly_scheduler.handler.count_games_in_week", return_value=0)
    mocker.patch("weekly_scheduler.handler.get_week_status", return_value=None)
    mocker.patch("weekly_scheduler.handler.get_active_admins", return_value=[])

    result = handler(_make_event(), None)

    assert result["body"]["weekStart"] == _current_week()


@pytest.mark.unit
def test_prompt_suppressed_when_week_at_floor(mocker):
    """When the week already has at least the floor number of games, no prompt is sent."""
    mocker.patch("weekly_scheduler.handler.count_games_in_week", return_value=1)
    mock_status = mocker.patch("weekly_scheduler.handler.get_week_status")
    mock_prompt = mocker.patch("weekly_scheduler.handler.send_admin_weekly_prompt")

    result = handler(_make_event(), None)

    assert result["body"]["action"] == "no_prompt"
    mock_prompt.assert_not_called()


@pytest.mark.unit
def test_prompt_suppressed_when_week_declined(mocker):
    """When a no-game decision is recorded, no prompt is sent even with zero games."""
    mocker.patch("weekly_scheduler.handler.count_games_in_week", return_value=0)
    mocker.patch(
        "weekly_scheduler.handler.get_week_status",
        return_value={"reason": "admin_declined"},
    )
    mock_prompt = mocker.patch("weekly_scheduler.handler.send_admin_weekly_prompt")

    result = handler(_make_event(), None)

    assert result["body"]["action"] == "no_prompt"
    mock_prompt.assert_not_called()


@pytest.mark.unit
def test_prompt_sent_to_multiple_admins(mocker):
    mocker.patch("weekly_scheduler.handler.count_games_in_week", return_value=0)
    mocker.patch("weekly_scheduler.handler.get_week_status", return_value=None)
    mocker.patch(
        "weekly_scheduler.handler.get_active_admins",
        return_value=[
            {"email": "admin1@example.com", "name": "Admin 1"},
            {"email": "admin2@example.com", "name": "Admin 2"},
        ],
    )
    mock_prompt = mocker.patch("weekly_scheduler.handler.send_admin_weekly_prompt")

    result = handler(_make_event(), None)

    assert mock_prompt.call_count == 2
    assert result["body"]["adminCount"] == 2


@pytest.mark.unit
def test_prompt_continues_if_one_send_fails(mocker):
    mocker.patch("weekly_scheduler.handler.count_games_in_week", return_value=0)
    mocker.patch("weekly_scheduler.handler.get_week_status", return_value=None)
    mocker.patch(
        "weekly_scheduler.handler.get_active_admins",
        return_value=[
            {"email": "admin1@example.com", "name": "Admin 1"},
            {"email": "admin2@example.com", "name": "Admin 2"},
        ],
    )
    mock_prompt = mocker.patch(
        "weekly_scheduler.handler.send_admin_weekly_prompt",
        side_effect=[Exception("SES error"), None],
    )

    result = handler(_make_event(), None)

    assert result["statusCode"] == 200
    assert mock_prompt.call_count == 2
