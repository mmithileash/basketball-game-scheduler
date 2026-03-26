from datetime import date, timedelta
from unittest.mock import call

import pytest

from announcement_sender.handler import _next_saturday, handler


@pytest.mark.unit
def test_handler_success(mocker):
    """Mock dynamo + email_service, verify create_game called, announcements sent."""
    mock_create_game = mocker.patch("announcement_sender.handler.create_game")
    mock_get_active = mocker.patch(
        "announcement_sender.handler.get_active_players",
        return_value=[
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
        ],
    )
    mock_send = mocker.patch("announcement_sender.handler.send_announcement")

    result = handler({}, None)

    assert result["statusCode"] == 200
    mock_create_game.assert_called_once()
    game_date = mock_create_game.call_args[0][0]
    assert result["body"]["gameDate"] == game_date
    assert result["body"]["playerCount"] == 2
    assert result["body"]["sentCount"] == 2

    assert mock_send.call_count == 2
    mock_send.assert_any_call("alice@example.com", "Alice", game_date)
    mock_send.assert_any_call("bob@example.com", "Bob", game_date)


@pytest.mark.unit
def test_handler_no_active_players(mocker):
    """Verify game still created but no emails sent."""
    mock_create_game = mocker.patch("announcement_sender.handler.create_game")
    mocker.patch("announcement_sender.handler.get_active_players", return_value=[])
    mock_send = mocker.patch("announcement_sender.handler.send_announcement")

    result = handler({}, None)

    assert result["statusCode"] == 200
    mock_create_game.assert_called_once()
    assert result["body"]["playerCount"] == 0
    assert result["body"]["sentCount"] == 0
    mock_send.assert_not_called()


@pytest.mark.unit
def test_handler_calculates_saturday():
    """Verify the next Saturday date calculation is correct."""
    result = _next_saturday()
    result_date = date.fromisoformat(result)

    # Result should be a Saturday (weekday() == 5)
    assert result_date.weekday() == 5

    # Result should be in the future
    today = date.today()
    assert result_date > today

    # Result should be within the next 7 days
    assert result_date <= today + timedelta(days=7)


@pytest.mark.unit
def test_handler_email_failure_doesnt_stop_others(mocker):
    """Verify that a failed email send doesn't prevent other emails."""
    mocker.patch("announcement_sender.handler.create_game")
    mocker.patch(
        "announcement_sender.handler.get_active_players",
        return_value=[
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
            {"email": "charlie@example.com", "name": "Charlie"},
        ],
    )
    mock_send = mocker.patch(
        "announcement_sender.handler.send_announcement",
        side_effect=[None, Exception("SES error"), None],
    )

    result = handler({}, None)

    assert result["body"]["sentCount"] == 2
    assert mock_send.call_count == 3
