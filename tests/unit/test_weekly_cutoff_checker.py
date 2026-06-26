import pytest

from weekly_cutoff_checker.handler import handler


def _make_event():
    return {}


@pytest.mark.unit
def test_no_game_sent_when_admin_has_not_responded(mocker):
    """When weekStatus missing or adminResponded=false, sends no-game to all players."""
    mocker.patch("weekly_cutoff_checker.handler.get_week_status", return_value=None)
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
    _, reason = mock_set.call_args[0]
    assert reason == "no_response"
    assert mock_notify.call_count == 2


@pytest.mark.unit
def test_noop_when_admin_already_responded(mocker):
    """When adminResponded=True, no emails are sent."""
    mocker.patch(
        "weekly_cutoff_checker.handler.get_week_status",
        return_value={"adminResponded": True, "gameCount": 1},
    )
    mock_set = mocker.patch("weekly_cutoff_checker.handler.set_week_no_game")
    mock_notify = mocker.patch("weekly_cutoff_checker.handler.send_no_game_this_week")

    result = handler(_make_event(), None)

    assert result["body"]["action"] == "already_responded"
    mock_set.assert_not_called()
    mock_notify.assert_not_called()


@pytest.mark.unit
def test_cutoff_continues_if_one_send_fails(mocker):
    """One failed email send does not abort notifying other players."""
    mocker.patch("weekly_cutoff_checker.handler.get_week_status", return_value=None)
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


@pytest.mark.unit
def test_week_start_targets_next_week(mocker):
    """The week start computed is 7 days out from today."""
    from datetime import date, timedelta
    from common.date_utils import week_start_for_date

    mocker.patch("weekly_cutoff_checker.handler.get_week_status", return_value=None)
    mocker.patch("weekly_cutoff_checker.handler.set_week_no_game")
    mocker.patch("weekly_cutoff_checker.handler.get_active_players", return_value=[])
    mock_notify = mocker.patch("weekly_cutoff_checker.handler.send_no_game_this_week")

    handler(_make_event(), None)

    today = date.today()
    expected_week = week_start_for_date(today + timedelta(days=7)).isoformat()

    set_no_game_call = mocker.patch("weekly_cutoff_checker.handler.set_week_no_game")
    # Verify the week_start passed to set_week_no_game matches expected
    # (Re-run to capture the value)
    mocker.patch("weekly_cutoff_checker.handler.get_week_status", return_value=None)
    mocker.patch("weekly_cutoff_checker.handler.get_active_players", return_value=[])
    handler(_make_event(), None)
    actual_week, _ = set_no_game_call.call_args[0]
    assert actual_week == expected_week
