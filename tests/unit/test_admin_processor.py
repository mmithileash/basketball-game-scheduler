import json
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from admin_processor.handler import handler


def _make_s3_event(bucket: str, key: str) -> dict:
    return {
        "Records": [{
            "s3": {
                "bucket": {"name": bucket},
                "object": {"key": key},
            }
        }]
    }


def _make_raw_email(from_addr: str, subject: str, body: str) -> bytes:
    return (
        f"From: {from_addr}\r\n"
        f"To: admin@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain\r\n"
        f"\r\n"
        f"{body}"
    ).encode()


@pytest.mark.unit
def test_non_admin_sender_is_rejected(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=False)
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("notadmin@example.com", "Cancel", "Cancel game"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 403
    mock_send.assert_called_once()
    call_args = mock_send.call_args[0]
    assert "permission" in call_args[2].lower() or "not authorised" in call_args[2].lower()


@pytest.mark.unit
def test_cancel_game_advance_creates_cancelled_record(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-04-11",
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mocker.patch("admin_processor.handler.get_game_status", return_value=None)
    mock_pre_cancel = mocker.patch("admin_processor.handler.pre_cancel_game")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Cancel", "Cancel game on April 11"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_pre_cancel.assert_called_once_with("2026-04-11")
    mock_send.assert_called_once()  # only the admin confirmation


@pytest.mark.unit
def test_cancel_game_open_updates_status_and_broadcasts(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-04-11",
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mocker.patch("admin_processor.handler.get_game_status", return_value={"gameDate": "2026-04-11", "status": "OPEN"})
    mock_update = mocker.patch("admin_processor.handler.update_game_status")
    mocker.patch("admin_processor.handler.get_roster", return_value={
        "YES": {"players": {"alice@example.com": {"name": "Alice"}, "bob@example.com": {"name": "Bob"}}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {"charlie@example.com": {"name": "Charlie"}}, "guests": []},
    })
    mock_send = mocker.patch("admin_processor.handler.send_admin_cancelled_broadcast")
    mock_send_email = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Cancel", "Cancel game on April 11"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_update.assert_called_once_with("2026-04-11", "CANCELLED")
    assert mock_send.call_count == 3
    mock_send.assert_any_call("alice@example.com", "2026-04-11")
    mock_send.assert_any_call("bob@example.com", "2026-04-11")
    mock_send.assert_any_call("charlie@example.com", "2026-04-11")
    mock_send_email.assert_called_once()


@pytest.mark.unit
def test_cancel_game_missing_date_sends_error(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Cancel", "Cancel the game"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_send.assert_called_once()
    assert "date" in mock_send.call_args[0][2].lower()


@pytest.mark.unit
def test_add_player_creates_record(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "ADD_PLAYER",
        "game_date": None,
        "email": "newplayer@example.com",
        "name": "New Player",
        "is_admin": False,
    })
    mock_add = mocker.patch("admin_processor.handler.add_player")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Add", "Add player newplayer@example.com New Player"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_add.assert_called_once_with("newplayer@example.com", "New Player", is_admin=False)
    mock_send.assert_called_once()


@pytest.mark.unit
def test_add_admin_creates_admin_record(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "ADD_ADMIN",
        "game_date": None,
        "email": "newadmin@example.com",
        "name": "New Admin",
        "is_admin": True,
    })
    mock_add = mocker.patch("admin_processor.handler.add_player")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Add Admin", "Add admin newadmin@example.com New Admin"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_add.assert_called_once_with("newadmin@example.com", "New Admin", is_admin=True)


@pytest.mark.unit
def test_deactivate_player(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "DEACTIVATE_PLAYER",
        "game_date": None,
        "email": "alice@example.com",
        "name": None,
        "is_admin": None,
    })
    mock_deactivate = mocker.patch("admin_processor.handler.deactivate_player")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Remove", "Deactivate alice@example.com"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_deactivate.assert_called_once_with("alice@example.com")


@pytest.mark.unit
def test_reactivate_player(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "REACTIVATE_PLAYER",
        "game_date": None,
        "email": "alice@example.com",
        "name": None,
        "is_admin": None,
    })
    mock_reactivate = mocker.patch("admin_processor.handler.reactivate_player")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Reactivate", "Reactivate alice@example.com"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_reactivate.assert_called_once_with("alice@example.com")


@pytest.mark.unit
def test_unknown_intent_sends_error_reply(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "UNKNOWN",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "??", "blahrgh"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_send.assert_called_once()
    assert "understand" in mock_send.call_args[0][2].lower()


@pytest.mark.unit
def test_cancel_game_notifies_guests_with_contact_email(mocker):
    """Cancelling an OPEN game also emails guests with their own contact email."""
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.get_game_status",
                 return_value={"status": "OPEN"})
    mocker.patch("admin_processor.handler.update_game_status")
    mocker.patch("admin_processor.handler.get_roster", return_value={
        "YES": {
            "players": {"alice@example.com": {"name": "Alice"}},
            "guests": [
                {"pk": "john@example.com", "sk": "guest#active", "name": "John",
                 "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
                {"pk": "alice@example.com", "sk": "guest#active#Jane", "name": "Jane",
                 "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
            ],
        },
        "MAYBE": {"players": {}, "guests": []},
    })
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-04-19",
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mock_broadcast = mocker.patch("admin_processor.handler.send_admin_cancelled_broadcast")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Cancel", "Cancel game 2026-04-19"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    broadcast_recipients = {call[0][0] for call in mock_broadcast.call_args_list}
    # alice (player) and john (guest with contact email) should be notified
    assert "alice@example.com" in broadcast_recipients
    assert "john@example.com" in broadcast_recipients
    # Jane has no own email (sk=guest#active#Jane), should NOT get a direct email
    assert broadcast_recipients == {"alice@example.com", "john@example.com"}
