import io
import json
from datetime import date
from email.mime.text import MIMEText
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from email_processor.handler import handler, _extract_email_body, _extract_sender_email


def _make_s3_event(bucket="test-email-bucket", key="test-message-id"):
    """Build a minimal S3 event matching what the handler expects."""
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key},
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
        "email_processor.handler.get_upcoming_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch(
        "email_processor.handler.get_roster",
        return_value={"YES": {"players": {}, "guests": []}, "NO": {"players": {}, "guests": []}, "MAYBE": {"players": {}, "guests": []}},
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
    mocker.patch("email_processor.handler.get_player_name", return_value="Alice")
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"]["intent"] == "JOIN"
    mock_update.assert_called_once_with(
        "2026-03-28", "alice@example.com", "YES", name="Alice", old_status=None
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
        "email_processor.handler.get_upcoming_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch(
        "email_processor.handler.get_roster",
        return_value={
            "YES": {"players": {"bob@example.com": {"name": "Bob"}}, "guests": []},
            "NO": {"players": {}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        },
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
    mocker.patch("email_processor.handler.get_player_name", return_value="Bob")
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mocker.patch("email_processor.handler.remove_sponsor_guests_from_status", return_value=[])
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["body"]["intent"] == "DECLINE"
    mock_update.assert_called_once_with(
        "2026-03-28", "bob@example.com", "NO", name="Bob", old_status="YES"
    )


@pytest.mark.unit
def test_handler_query_roster(mocker):
    """Verify no DB update for QUERY_ROSTER, reply sent."""
    raw_email = _make_raw_email("alice@example.com", "Re: Basketball Game", "Who's playing?")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)

    mocker.patch(
        "email_processor.handler.get_upcoming_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch(
        "email_processor.handler.get_roster",
        return_value={"YES": {"players": {}, "guests": []}, "NO": {"players": {}, "guests": []}, "MAYBE": {"players": {}, "guests": []}},
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

    result = handler(_make_s3_event(), None)

    assert result["body"]["intent"] == "QUERY_ROSTER"
    mock_update.assert_not_called()
    mock_send.assert_called_once()


@pytest.mark.unit
@mock_aws
def test_handler_cancelled_game_response(mocker):
    """Player replies to a CANCELLED game: handler must NOT include the
    roster of players going to the game in the reply, and must NOT update
    any RSVP state. This exercises the real get_current_open_game path
    against a moto-backed DynamoDB so the OPEN-status filter is covered
    end-to-end at the handler layer.
    """
    # --- Set up moto-backed DynamoDB tables ---
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    dynamodb.create_table(
        TableName="test-players",
        KeySchema=[
            {"AttributeName": "email", "KeyType": "HASH"},
            {"AttributeName": "active", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "email", "AttributeType": "S"},
            {"AttributeName": "active", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamodb.create_table(
        TableName="test-games",
        KeySchema=[
            {"AttributeName": "gameDate", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "gameDate", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Reset cached boto3 clients/resources so they bind to moto
    import common.dynamo as dynamo_mod
    dynamo_mod._config = None
    dynamo_mod._dynamodb = None
    dynamo_mod._client = None

    # --- Pin "today" so the upcoming Saturday is deterministic ---
    fake_today = date(2026, 4, 8)  # Wednesday
    fake_saturday = "2026-04-11"
    mocker.patch("common.dynamo.date", wraps=date).today.return_value = fake_today

    # --- Seed an upcoming-Saturday game with confirmed players, then cancel it ---
    from common.dynamo import create_game, update_game_status, update_player_response
    create_game(fake_saturday)
    update_player_response(fake_saturday, "alice@example.com", "YES", name="Alice")
    update_player_response(fake_saturday, "bob@example.com", "YES", name="Bob")
    update_game_status(fake_saturday, "CANCELLED")

    # --- Mock S3 fetch of the inbound email ---
    raw_email = _make_raw_email(
        "charlie@example.com", "Re: Basketball Game", "I'm in!"
    )
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)

    # parse_player_email and update_player_response must NOT be called for a
    # cancelled game — patch them so we can assert that.
    mock_parse = mocker.patch("email_processor.handler.parse_player_email")
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    # --- Act ---
    result = handler(_make_s3_event(), None)

    # --- Assert: short-circuit, no Bedrock call, no RSVP write ---
    assert result["statusCode"] == 200
    assert result["body"] == "Game cancelled"
    mock_parse.assert_not_called()
    mock_update.assert_not_called()

    # --- Assert: reply explicitly mentions the cancellation, names the date,
    #     and contains NO roster / player names ---
    mock_send.assert_called_once()
    to_addr, subject, body = mock_send.call_args[0]
    assert to_addr == "charlie@example.com"
    assert "Re:" in subject
    assert "cancelled" in body.lower()
    assert fake_saturday in body  # cancellation message must name the game date
    # The reply must not leak the roster of players going to the game
    assert "Alice" not in body
    assert "Bob" not in body
    assert "alice@example.com" not in body
    assert "bob@example.com" not in body
    assert "Playing" not in body  # _format_roster_summary section header
    assert "Current Responses" not in body  # _format_roster_summary header


@pytest.mark.unit
def test_handler_cancelled_game_query_roster(mocker):
    """A player asks 'Who's playing?' for a CANCELLED game.

    Same short-circuit as any other intent: handler must NOT call Bedrock,
    must NOT leak the (former) roster, and must reply with the cancellation
    message — even though the player explicitly asked for the roster.
    """
    raw_email = _make_raw_email(
        "charlie@example.com", "Re: Basketball Game", "Who's playing this week?"
    )
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)

    mocker.patch(
        "email_processor.handler.get_upcoming_game",
        return_value={"gameDate": "2026-04-11", "status": "CANCELLED"},
    )
    mock_parse = mocker.patch("email_processor.handler.parse_player_email")
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_get_roster = mocker.patch("email_processor.handler.get_roster")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"] == "Game cancelled"
    # Critical: Bedrock is never invoked (the intent is never classified),
    # and no roster lookup happens.
    mock_parse.assert_not_called()
    mock_update.assert_not_called()
    mock_get_roster.assert_not_called()

    mock_send.assert_called_once()
    body = mock_send.call_args[0][2]
    assert "cancelled" in body.lower()
    assert "2026-04-11" in body
    # No roster section, even though the player asked for one
    assert "Playing" not in body
    assert "Current Responses" not in body


@pytest.mark.unit
def test_handler_no_open_game(mocker):
    """Verify early return if no open game."""
    raw_email = _make_raw_email("alice@example.com", "Re: Basketball Game", "I'm in!")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)

    mocker.patch("email_processor.handler.get_upcoming_game", return_value=None)
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

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


@pytest.mark.unit
def test_bring_guests_creates_player_entries(mocker):
    """BRING_GUESTS creates guest Players entries and adds to YES guests array."""
    bedrock_result = {
        "intent": "BRING_GUESTS",
        "guests": [
            {"name": "John", "contact_email": "john@example.com"},
            {"name": "Jane", "contact_email": None},
        ],
        "confirmed_guest_names": [],
        "query_target": None,
        "reply_draft": "Got it!",
    }

    raw_email = _make_raw_email("alice@example.com", "Re: Game", "I'm in, bringing John and Jane")

    mocker.patch("email_processor.handler._get_s3_client").return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: raw_email)
    }
    mocker.patch("email_processor.handler.get_upcoming_game").return_value = {"gameDate": "2026-04-05", "status": "OPEN"}
    mocker.patch("email_processor.handler.get_roster").return_value = {
        "YES": {"players": {}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    }
    mocker.patch("email_processor.handler.parse_player_email").return_value = bedrock_result
    mocker.patch("email_processor.handler.get_player_name").return_value = "Alice"
    mocker.patch("email_processor.handler.update_player_response")
    mock_create = mocker.patch("email_processor.handler.create_guest_entry")
    mock_create.side_effect = [
        {"pk": "john@example.com", "sk": "guest#active", "name": "John",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
        {"pk": "alice@example.com", "sk": "guest#active#Jane", "name": "Jane",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
    ]
    mock_add = mocker.patch("email_processor.handler.add_guests_to_game_status")
    mocker.patch("email_processor.handler.send_email")

    from email_processor.handler import handler
    result = handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}]}, None)

    assert result["statusCode"] == 200
    assert mock_create.call_count == 2
    mock_create.assert_any_call("2026-04-05", "John", "alice@example.com", "Alice", "john@example.com")
    mock_create.assert_any_call("2026-04-05", "Jane", "alice@example.com", "Alice", None)
    mock_add.assert_called_once()
    call_args = mock_add.call_args
    assert call_args[0][1] == "YES"
    assert len(call_args[0][2]) == 2


@pytest.mark.unit
def test_decline_with_guests_moves_to_no_and_sends_followup():
    """DECLINE when player has guests: moves guests to NO, sends follow-up email."""
    from unittest.mock import MagicMock, patch

    bedrock_result = {
        "intent": "DECLINE",
        "guests": [],
        "confirmed_guest_names": [],
        "query_target": None,
        "reply_draft": "Sorry to hear that!",
    }

    yes_guests = [
        {"pk": "john@example.com", "sk": "guest#active", "name": "John",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
    ]
    raw_email = _make_raw_email("alice@example.com", "Re: Game", "Can't make it")

    with patch("email_processor.handler._get_s3_client") as mock_s3_fn, \
         patch("email_processor.handler.get_upcoming_game") as mock_game, \
         patch("email_processor.handler.get_roster") as mock_roster, \
         patch("email_processor.handler.parse_player_email") as mock_parse, \
         patch("email_processor.handler.get_player_name") as mock_name, \
         patch("email_processor.handler.update_player_response") as mock_update, \
         patch("email_processor.handler.remove_sponsor_guests_from_status") as mock_remove, \
         patch("email_processor.handler.add_guests_to_game_status") as mock_add, \
         patch("email_processor.handler.send_email") as mock_send_email, \
         patch("email_processor.handler.send_guest_followup") as mock_followup:

        mock_s3_fn.return_value.get_object.return_value = {"Body": MagicMock(read=lambda: raw_email)}
        mock_game.return_value = {"gameDate": "2026-04-05", "status": "OPEN"}
        mock_roster.return_value = {
            "YES": {"players": {"alice@example.com": {"name": "Alice"}}, "guests": yes_guests},
            "NO": {"players": {}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        }
        mock_parse.return_value = bedrock_result
        mock_name.return_value = "Alice"
        mock_remove.return_value = yes_guests

        from email_processor.handler import handler
        result = handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}]}, None)

    assert result["statusCode"] == 200
    mock_remove.assert_called_once_with("2026-04-05", "YES", "alice@example.com")
    mock_add.assert_called_once_with("2026-04-05", "NO", yes_guests)
    mock_followup.assert_called_once_with(
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        guest_names=["John"],
        game_date="2026-04-05",
    )


@pytest.mark.unit
def test_decline_without_guests_no_followup():
    """DECLINE when player has no guests: normal decline, no follow-up sent."""
    from unittest.mock import MagicMock, patch

    bedrock_result = {
        "intent": "DECLINE",
        "guests": [],
        "confirmed_guest_names": [],
        "query_target": None,
        "reply_draft": "Sorry to hear that!",
    }
    raw_email = _make_raw_email("alice@example.com", "Re: Game", "Can't make it")

    with patch("email_processor.handler._get_s3_client") as mock_s3_fn, \
         patch("email_processor.handler.get_upcoming_game") as mock_game, \
         patch("email_processor.handler.get_roster") as mock_roster, \
         patch("email_processor.handler.parse_player_email") as mock_parse, \
         patch("email_processor.handler.get_player_name") as mock_name, \
         patch("email_processor.handler.update_player_response") as mock_update, \
         patch("email_processor.handler.remove_sponsor_guests_from_status") as mock_remove, \
         patch("email_processor.handler.send_email") as mock_send, \
         patch("email_processor.handler.send_guest_followup") as mock_followup:

        mock_s3_fn.return_value.get_object.return_value = {"Body": MagicMock(read=lambda: raw_email)}
        mock_game.return_value = {"gameDate": "2026-04-05", "status": "OPEN"}
        mock_roster.return_value = {
            "YES": {"players": {"alice@example.com": {"name": "Alice"}}, "guests": []},
            "NO": {"players": {}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        }
        mock_parse.return_value = bedrock_result
        mock_name.return_value = "Alice"
        mock_remove.return_value = []

        from email_processor.handler import handler
        handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}]}, None)

    mock_followup.assert_not_called()


@pytest.mark.unit
def test_guest_confirm_moves_guests_to_yes():
    """GUEST_CONFIRM moves confirmed guests from NO to YES."""
    from unittest.mock import MagicMock, patch

    bedrock_result = {
        "intent": "GUEST_CONFIRM",
        "guests": [],
        "confirmed_guest_names": ["John"],
        "query_target": None,
        "reply_draft": "John is still coming!",
    }
    raw_email = _make_raw_email("alice@example.com", "Re: Your guests", "John is still coming")

    with patch("email_processor.handler._get_s3_client") as mock_s3_fn, \
         patch("email_processor.handler.get_upcoming_game") as mock_game, \
         patch("email_processor.handler.get_roster") as mock_roster, \
         patch("email_processor.handler.parse_player_email") as mock_parse, \
         patch("email_processor.handler.get_player_name") as mock_name, \
         patch("email_processor.handler.move_confirmed_guests") as mock_move, \
         patch("email_processor.handler.send_email") as mock_send:

        mock_s3_fn.return_value.get_object.return_value = {"Body": MagicMock(read=lambda: raw_email)}
        mock_game.return_value = {"gameDate": "2026-04-05", "status": "OPEN"}
        mock_roster.return_value = {
            "YES": {"players": {}, "guests": []},
            "NO": {"players": {"alice@example.com": {"name": "Alice"}}, "guests": [
                {"pk": "john@example.com", "sk": "guest#active", "name": "John",
                 "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
            ]},
            "MAYBE": {"players": {}, "guests": []},
        }
        mock_parse.return_value = bedrock_result
        mock_name.return_value = "Alice"

        from email_processor.handler import handler
        result = handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}]}, None)

    assert result["statusCode"] == 200
    mock_move.assert_called_once_with("2026-04-05", "alice@example.com", ["John"])


@pytest.mark.unit
def test_guest_confirm_with_no_confirmed_names_does_not_move_guests():
    """GUEST_CONFIRM with empty confirmed_guest_names: no move, correct reply sent."""
    from unittest.mock import MagicMock, patch

    bedrock_result = {
        "intent": "GUEST_CONFIRM",
        "guests": [],
        "confirmed_guest_names": [],
        "query_target": None,
        "reply_draft": "Got your message!",
    }
    raw_email = _make_raw_email("alice@example.com", "Re: Your guests", "unsure")

    with patch("email_processor.handler._get_s3_client") as mock_s3_fn, \
         patch("email_processor.handler.get_upcoming_game") as mock_game, \
         patch("email_processor.handler.get_roster") as mock_roster, \
         patch("email_processor.handler.parse_player_email") as mock_parse, \
         patch("email_processor.handler.get_player_name") as mock_name, \
         patch("email_processor.handler.move_confirmed_guests") as mock_move, \
         patch("email_processor.handler.send_email") as mock_send:

        mock_s3_fn.return_value.get_object.return_value = {"Body": MagicMock(read=lambda: raw_email)}
        mock_game.return_value = {"gameDate": "2026-04-05", "status": "OPEN"}
        mock_roster.return_value = {
            "YES": {"players": {}, "guests": []},
            "NO": {"players": {"alice@example.com": {"name": "Alice"}}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        }
        mock_parse.return_value = bedrock_result
        mock_name.return_value = "Alice"

        from email_processor.handler import handler
        result = handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}]}, None)

    assert result["statusCode"] == 200
    mock_move.assert_not_called()
    # Verify the reply does not contain the malformed "attending: ." text
    sent_body = mock_send.call_args[0][2]
    assert "attending: ." not in sent_body
    assert "We've noted your message about your guests." in sent_body
