from unittest.mock import call

import pytest

from announcement_sender.handler import handler


@pytest.mark.unit
def test_handler_success(mocker):
    """Mock dynamo + email_service, verify create_game called, announcements sent."""
    mocker.patch("announcement_sender.handler.get_game_status", return_value=None)
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
    mocker.patch("announcement_sender.handler.get_game_status", return_value=None)
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
def test_handler_email_failure_doesnt_stop_others(mocker):
    """Verify that a failed email send doesn't prevent other emails."""
    mocker.patch("announcement_sender.handler.get_game_status", return_value=None)
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


@pytest.mark.unit
def test_handler_skips_pre_cancelled_game(mocker):
    """If the upcoming Saturday is pre-cancelled, send no-game email instead of announcement."""
    mocker.patch(
        "announcement_sender.handler.get_game_status",
        return_value={"gameDate": "2026-04-11", "sk": "gameStatus", "status": "CANCELLED"},
    )
    mock_create_game = mocker.patch("announcement_sender.handler.create_game")
    mock_get_active = mocker.patch(
        "announcement_sender.handler.get_active_players",
        return_value=[
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
        ],
    )
    mock_no_game = mocker.patch("announcement_sender.handler.send_no_game_announcement")
    mock_announcement = mocker.patch("announcement_sender.handler.send_announcement")

    result = handler({}, None)

    assert result["statusCode"] == 200
    assert result["body"]["action"] == "pre_cancelled"
    mock_create_game.assert_not_called()
    mock_announcement.assert_not_called()
    mock_get_active.assert_called_once()
    assert mock_no_game.call_count == 2
    mock_no_game.assert_any_call("alice@example.com", "Alice", mocker.ANY)
    mock_no_game.assert_any_call("bob@example.com", "Bob", mocker.ANY)


@pytest.mark.unit
def test_handler_proceeds_normally_when_no_pre_cancel(mocker):
    """If no game record exists, proceed with normal game creation."""
    mocker.patch("announcement_sender.handler.get_game_status", return_value=None)
    mock_create_game = mocker.patch("announcement_sender.handler.create_game")
    mocker.patch(
        "announcement_sender.handler.get_active_players",
        return_value=[{"email": "alice@example.com", "name": "Alice"}],
    )
    mock_announcement = mocker.patch("announcement_sender.handler.send_announcement")

    result = handler({}, None)

    assert result["statusCode"] == 200
    mock_create_game.assert_called_once()
    mock_announcement.assert_called_once()
