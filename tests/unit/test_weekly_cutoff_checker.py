from datetime import date

import pytest

from common.date_utils import week_start_for_date
from weekly_cutoff_checker.handler import handler


def _make_event():
    return {}


def _current_week():
    return week_start_for_date(date.today()).isoformat()


@pytest.mark.unit
def test_no_game_sent_for_genuinely_empty_week(mocker):
    """Zero games and no prior decision → mark no_response and notify all players."""
    mocker.patch("weekly_cutoff_checker.handler.get_week_status", return_value=None)
    mocker.patch("weekly_cutoff_checker.handler.count_games_in_week", return_value=0)
    mock_set = mocker.patch("weekly_cutoff_checker.handler.set_week_no_game")
    mocker.patch(
        "weekly_cutoff_checker.handler.get_active_players",
        return_value=[
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
        ],
    )
    mock_notify = mocker.patch("weekly_cutoff_checker.handler.send_no_game_this_week")

    result = handler(_make_event(), None)

    assert result["statusCode"] == 200
    assert result["body"]["action"] == "no_game_sent"
    mock_set.assert_called_once()
    week, reason = mock_set.call_args[0]
    assert week == _current_week()  # current week, not next
    assert reason == "no_response"
    assert mock_notify.call_count == 2


@pytest.mark.unit
def test_noop_when_decision_already_recorded(mocker):
    """A recorded no-game decision makes the cutoff idempotent — no re-send."""
    mocker.patch(
        "weekly_cutoff_checker.handler.get_week_status",
        return_value={"reason": "admin_declined"},
    )
    mock_count = mocker.patch("weekly_cutoff_checker.handler.count_games_in_week")
    mock_set = mocker.patch("weekly_cutoff_checker.handler.set_week_no_game")
    mock_notify = mocker.patch("weekly_cutoff_checker.handler.send_no_game_this_week")

    result = handler(_make_event(), None)

    assert result["body"]["action"] == "already_handled"
    mock_set.assert_not_called()
    mock_notify.assert_not_called()


@pytest.mark.unit
def test_noop_when_week_has_games(mocker):
    """When the week has at least one live game, no 'no game' broadcast is sent."""
    mocker.patch("weekly_cutoff_checker.handler.get_week_status", return_value=None)
    mocker.patch("weekly_cutoff_checker.handler.count_games_in_week", return_value=1)
    mock_set = mocker.patch("weekly_cutoff_checker.handler.set_week_no_game")
    mock_notify = mocker.patch("weekly_cutoff_checker.handler.send_no_game_this_week")

    result = handler(_make_event(), None)

    assert result["body"]["action"] == "games_scheduled"
    mock_set.assert_not_called()
    mock_notify.assert_not_called()


@pytest.mark.unit
def test_cutoff_continues_if_one_send_fails(mocker):
    mocker.patch("weekly_cutoff_checker.handler.get_week_status", return_value=None)
    mocker.patch("weekly_cutoff_checker.handler.count_games_in_week", return_value=0)
    mocker.patch("weekly_cutoff_checker.handler.set_week_no_game")
    mocker.patch(
        "weekly_cutoff_checker.handler.get_active_players",
        return_value=[
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
        ],
    )
    mock_notify = mocker.patch(
        "weekly_cutoff_checker.handler.send_no_game_this_week",
        side_effect=[Exception("SES error"), None],
    )

    result = handler(_make_event(), None)

    assert result["statusCode"] == 200
    assert mock_notify.call_count == 2
