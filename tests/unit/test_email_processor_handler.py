import io
import json
from email.mime.text import MIMEText

import pytest

from email_processor.handler import handler, _extract_email_body, _extract_sender_email


def _make_ses_event(message_id="test-message-id"):
    """Build a minimal SES inbound event."""
    return {
        "Records": [
            {
                "ses": {
                    "mail": {
                        "messageId": message_id,
                    }
                }
            }
        ]
    }


def _make_raw_email(from_addr, subject, body):
    """Build a raw email as bytes."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = subject
    return msg.as_bytes()


@pytest.mark.unit
def test_handler_join(mocker):
    """Mock S3 email retrieval, mock Bedrock response as JOIN, verify RSVP updated."""
    raw_email = _make_raw_email("alice@example.com", "Re: Basketball Game", "I'm in!")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)

    mocker.patch(
        "email_processor.handler.get_current_open_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch(
        "email_processor.handler.get_roster",
        return_value={"YES": {}, "NO": {}, "MAYBE": {}},
    )
    mocker.patch(
        "email_processor.handler.parse_player_email",
        return_value={
            "intent": "JOIN",
            "guest_count": 0,
            "guest_names": [],
            "query_target": None,
            "reply_draft": "You're in!",
        },
    )
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_ses_event(), None)

    assert result["statusCode"] == 200
    assert result["body"]["intent"] == "JOIN"
    mock_update.assert_called_once_with(
        "2026-03-28", "alice@example.com", "YES", guests=None, old_status=None
    )
    mock_send.assert_called_once()


@pytest.mark.unit
def test_handler_decline(mocker):
    """Mock Bedrock response as DECLINE, verify RSVP updated."""
    raw_email = _make_raw_email("bob@example.com", "Re: Basketball Game", "Can't make it")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)

    mocker.patch(
        "email_processor.handler.get_current_open_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch(
        "email_processor.handler.get_roster",
        return_value={"YES": {"bob@example.com": {"guests": []}}, "NO": {}, "MAYBE": {}},
    )
    mocker.patch(
        "email_processor.handler.parse_player_email",
        return_value={
            "intent": "DECLINE",
            "guest_count": 0,
            "guest_names": [],
            "query_target": None,
            "reply_draft": "Sorry to hear that!",
        },
    )
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_ses_event(), None)

    assert result["body"]["intent"] == "DECLINE"
    mock_update.assert_called_once_with(
        "2026-03-28", "bob@example.com", "NO", guests=None, old_status="YES"
    )


@pytest.mark.unit
def test_handler_query_roster(mocker):
    """Verify no DB update for QUERY_ROSTER, reply sent."""
    raw_email = _make_raw_email("alice@example.com", "Re: Basketball Game", "Who's playing?")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)

    mocker.patch(
        "email_processor.handler.get_current_open_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch(
        "email_processor.handler.get_roster",
        return_value={"YES": {}, "NO": {}, "MAYBE": {}},
    )
    mocker.patch(
        "email_processor.handler.parse_player_email",
        return_value={
            "intent": "QUERY_ROSTER",
            "guest_count": 0,
            "guest_names": [],
            "query_target": None,
            "reply_draft": "Here's the current roster...",
        },
    )
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_ses_event(), None)

    assert result["body"]["intent"] == "QUERY_ROSTER"
    mock_update.assert_not_called()
    mock_send.assert_called_once()


@pytest.mark.unit
def test_handler_no_open_game(mocker):
    """Verify early return if no open game."""
    raw_email = _make_raw_email("alice@example.com", "Re: Basketball Game", "I'm in!")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)

    mocker.patch("email_processor.handler.get_current_open_game", return_value=None)
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_ses_event(), None)

    assert result["statusCode"] == 200
    assert result["body"] == "No open game"
    mock_update.assert_not_called()
    # A "no game" reply is still sent
    mock_send.assert_called_once()
    reply_body = mock_send.call_args[0][2]
    assert "no game" in reply_body.lower() or "no game" in reply_body.lower()


@pytest.mark.unit
def test_handler_email_parsing():
    """Test that email body extraction works for plain text."""
    msg = MIMEText("I'm playing this week!", "plain", "utf-8")
    msg["From"] = "Test Player <test@example.com>"
    msg["Subject"] = "Re: Basketball"

    import email as email_lib
    from email import policy
    parsed = email_lib.message_from_bytes(msg.as_bytes(), policy=policy.default)

    body = _extract_email_body(parsed)
    assert "I'm playing this week!" in body


@pytest.mark.unit
def test_extract_sender_email_with_name():
    """Test extracting email from 'Name <email>' format."""
    assert _extract_sender_email("Alice Smith <alice@example.com>") == "alice@example.com"


@pytest.mark.unit
def test_extract_sender_email_plain():
    """Test extracting plain email address."""
    assert _extract_sender_email("alice@example.com") == "alice@example.com"
