import io
import json
from datetime import date
from email.mime.text import MIMEText
from unittest.mock import MagicMock

import pytest

from common.email_utils import extract_email_body, extract_sender_email
from email_processor.handler import handler


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
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")

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
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")

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
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")

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
def test_handler_cancelled_game_response(mocker, dynamodb_tables):
    """Player replies to a CANCELLED game with a JOIN-style message: handler
    must reply with the cancellation message, write no RSVP state, and leak
    no roster. Exercises the real get_upcoming_game -> get_game_status path
    against a moto-backed DynamoDB.
    """
    # Pin "today" so _next_saturday() resolves to the seeded game date.
    fake_today = date(2026, 4, 8)  # Wednesday
    fake_saturday = "2026-04-11"
    mocker.patch("common.dynamo.date", wraps=date).today.return_value = fake_today

    from common.dynamo import add_player, create_game, update_game_status, update_player_response
    add_player("charlie@example.com", "Charlie")
    create_game(fake_saturday)
    update_player_response(fake_saturday, "alice@example.com", "YES", name="Alice")
    update_player_response(fake_saturday, "bob@example.com", "YES", name="Bob")
    update_game_status(fake_saturday, "CANCELLED")

    raw_email = _make_raw_email(
        "charlie@example.com", "Re: Basketball Game", "I'm in!"
    )
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)

    mock_parse = mocker.patch("email_processor.handler.parse_player_email")
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"] == "Game cancelled"
    mock_parse.assert_not_called()
    mock_update.assert_not_called()

    mock_send.assert_called_once()
    to_addr, subject, body = mock_send.call_args[0]
    assert to_addr == "charlie@example.com"
    assert "Re:" in subject
    assert "cancelled" in body.lower()
    assert fake_saturday in body
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
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")

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
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")

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

    body = extract_email_body(parsed)
    assert "I'm playing this week!" in body


@pytest.mark.unit
def testextract_sender_email_with_name():
    """Test extracting email from 'Name <email>' format."""
    assert extract_sender_email("Alice Smith <alice@example.com>") == "alice@example.com"


@pytest.mark.unit
def testextract_sender_email_plain():
    """Test extracting plain email address."""
    assert extract_sender_email("alice@example.com") == "alice@example.com"


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
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
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
         patch("email_processor.handler.get_sender_role", return_value="player"), \
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
         patch("email_processor.handler.get_sender_role", return_value="player"), \
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
         patch("email_processor.handler.get_sender_role", return_value="player"), \
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
         patch("email_processor.handler.get_sender_role", return_value="player"), \
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


@pytest.mark.unit
def test_html_to_text_inserts_newlines_at_block_tags():
    """_html_to_text turns block-level tags into line breaks so that
    EmailReplyParser's line-based heuristics can later see quote markers
    that originated as <blockquote> elements.
    """
    from common.email_utils import _html_to_text

    html = (
        '<div>I\'m in!</div>'
        '<div class="gmail_quote">'
        '<div>On Mon, Apr 8, 2026, Scheduler &lt;scheduler@example.com&gt; wrote:</div>'
        '<blockquote>Are you playing this Saturday?</blockquote>'
        '</div>'
    )
    text = _html_to_text(html)

    assert "I'm in!" in text
    assert "On Mon, Apr 8, 2026, Scheduler <scheduler@example.com> wrote:" in text
    assert "Are you playing this Saturday?" in text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    assert "I'm in!" in lines
    assert "Are you playing this Saturday?" in lines


@pytest.mark.unit
def test_extract_body_strips_on_wrote_quote():
    """A plain-text reply with an 'On ... wrote:' quoted block keeps only
    the new content above the quote line.
    """


    body = (
        "I'm in!\n"
        "\n"
        "On Mon, Apr 8, 2026 at 9:00 AM, Scheduler <scheduler@example.com> wrote:\n"
        "> Are you playing this Saturday?\n"
        "> Reply YES to confirm."
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Re: Basketball Game"

    import email as email_lib
    from email import policy
    parsed = email_lib.message_from_bytes(msg.as_bytes(), policy=policy.default)

    extracted = extract_email_body(parsed)
    assert extracted.strip() == "I'm in!"
    assert "Scheduler" not in extracted
    assert "Reply YES" not in extracted


@pytest.mark.unit
def test_extract_body_strips_gt_quoted_lines():
    """A plain-text reply where the prior message is line-quoted with '>'
    keeps only the user's new content.
    """


    body = (
        "Sure I'll bring 2 friends\n"
        "\n"
        "> On 2026-04-08, Scheduler wrote:\n"
        "> Reminder: game on Saturday at 10 AM\n"
        "> Bring water"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Re: Basketball Game"

    import email as email_lib
    from email import policy
    parsed = email_lib.message_from_bytes(msg.as_bytes(), policy=policy.default)

    extracted = extract_email_body(parsed)
    assert "Sure I'll bring 2 friends" in extracted
    assert "Reminder" not in extracted
    assert "Bring water" not in extracted


@pytest.mark.unit
def test_extract_body_html_fallback_strips_quotes():
    """An HTML-only reply: the HTML is converted to text first, then
    quoted history is stripped. The user's new content survives.
    """
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText as MIMETextHelper



    html = (
        '<div>I\'m in!</div>'
        '<div class="gmail_quote">'
        '<div>On Mon, Apr 8, 2026, Scheduler &lt;scheduler@example.com&gt; wrote:</div>'
        '<blockquote>Are you playing this Saturday?</blockquote>'
        '</div>'
    )

    msg = MIMEMultipart("alternative")
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Re: Basketball Game"
    msg.attach(MIMETextHelper(html, "html", "utf-8"))

    import email as email_lib
    from email import policy
    parsed = email_lib.message_from_bytes(msg.as_bytes(), policy=policy.default)

    extracted = extract_email_body(parsed)
    assert "I'm in!" in extracted
    assert "Are you playing" not in extracted
    assert "Scheduler" not in extracted


@pytest.mark.unit
def test_handler_unknown_sender_rejected(mocker):
    """Unknown senders receive a rejection email and no Bedrock call is made."""
    raw_email = _make_raw_email("stranger@example.com", "Re: Basketball Game", "I'm in!")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="unknown")

    mock_parse = mocker.patch("email_processor.handler.parse_player_email")
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 403
    mock_parse.assert_not_called()
    mock_update.assert_not_called()
    mock_send.assert_called_once()
    body = mock_send.call_args[0][2]
    assert "not a registered player" in body.lower()


@pytest.mark.unit
def test_handler_guest_decline_cancels_and_notifies_sponsor(mocker):
    """Guest DECLINE removes them from YES, moves to NO, and notifies the sponsor."""
    raw_email = _make_raw_email("john@example.com", "Re: Basketball Game", "Can't make it")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="guest")
    mocker.patch(
        "email_processor.handler.get_upcoming_game",
        return_value={"gameDate": "2026-04-19", "status": "OPEN"},
    )
    guest_obj = {"pk": "john@example.com", "sk": "guest#active", "name": "John",
                 "sponsorEmail": "alice@example.com", "sponsorName": "Alice"}
    mocker.patch("email_processor.handler.get_roster", return_value={
        "YES": {"players": {}, "guests": [guest_obj]},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    })
    mocker.patch("email_processor.handler.parse_player_email", return_value={
        "intent": "DECLINE", "reply_draft": "Sorry to hear that!",
        "guests": [], "confirmed_guest_names": [], "query_target": None,
    })
    mock_remove = mocker.patch("email_processor.handler.remove_guest_from_status", return_value=guest_obj)
    mock_add = mocker.patch("email_processor.handler.add_guests_to_game_status")
    mocker.patch("email_processor.handler.get_player_name", return_value="Alice")
    mock_notify = mocker.patch("email_processor.handler.send_guest_cancelled_sponsor_notification")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    mock_remove.assert_called_once_with("2026-04-19", "YES", "john@example.com")
    mock_add.assert_called_once_with("2026-04-19", "NO", [guest_obj])
    mock_notify.assert_called_once_with("alice@example.com", "Alice", "John", "2026-04-19")
    mock_send.assert_called_once()
    assert mock_send.call_args[0][0] == "john@example.com"


@pytest.mark.unit
def test_handler_guest_query_roster_allowed(mocker):
    """Guest QUERY_ROSTER is allowed and gets a roster reply."""
    raw_email = _make_raw_email("john@example.com", "Re: Basketball Game", "Who's playing?")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="guest")
    mocker.patch(
        "email_processor.handler.get_upcoming_game",
        return_value={"gameDate": "2026-04-19", "status": "OPEN"},
    )
    mocker.patch("email_processor.handler.get_roster", return_value={
        "YES": {"players": {}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    })
    mocker.patch("email_processor.handler.parse_player_email", return_value={
        "intent": "QUERY_ROSTER", "reply_draft": "Here's who's playing...",
        "guests": [], "confirmed_guest_names": [], "query_target": None,
    })
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"]["intent"] == "QUERY_ROSTER"
    mock_update.assert_not_called()
    mock_send.assert_called_once()


@pytest.mark.unit
def test_handler_guest_join_rejected(mocker):
    """Guest attempting to JOIN gets a restriction message, no roster update."""
    raw_email = _make_raw_email("john@example.com", "Re: Basketball Game", "I'm in!")

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="guest")
    mocker.patch(
        "email_processor.handler.get_upcoming_game",
        return_value={"gameDate": "2026-04-19", "status": "OPEN"},
    )
    mocker.patch("email_processor.handler.get_roster", return_value={
        "YES": {"players": {}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    })
    mocker.patch("email_processor.handler.parse_player_email", return_value={
        "intent": "JOIN", "reply_draft": "You're in!",
        "guests": [], "confirmed_guest_names": [], "query_target": None,
    })
    mock_update = mocker.patch("email_processor.handler.update_player_response")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    mock_update.assert_not_called()
    body = mock_send.call_args[0][2]
    assert "only cancel" in body.lower() or "as a guest" in body.lower()


# ---------------------------------------------------------------------------
# UNSUBSCRIBE handler tests
# ---------------------------------------------------------------------------

def _make_unsubscribe_email(from_addr: str, subject: str = "UNSUBSCRIBE") -> bytes:
    from email.mime.text import MIMEText
    msg = MIMEText("", "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = subject
    return msg.as_bytes()


@pytest.mark.unit
def test_handler_unsubscribe_active_player(mocker):
    """UNSUBSCRIBE subject from an active player deactivates them and sends confirmation."""
    import io
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(_make_unsubscribe_email("alice@example.com"))}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
    mock_deactivate = mocker.patch("email_processor.handler.deactivate_player")
    mocker.patch("email_processor.handler.get_active_admins", return_value=[])
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"] == "Unsubscribed"
    mock_deactivate.assert_called_once_with("alice@example.com")
    sent_to = [c[0][0] for c in mock_send.call_args_list]
    assert "alice@example.com" in sent_to
    player_call = next(c for c in mock_send.call_args_list if c[0][0] == "alice@example.com")
    assert "unsubscribed" in player_call[0][2].lower()
    assert "organiser" in player_call[0][2].lower()


@pytest.mark.unit
def test_handler_unsubscribe_case_insensitive(mocker):
    """Subject 'unsubscribe' (lowercase) triggers the same handler."""
    import io
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(_make_unsubscribe_email("alice@example.com", "unsubscribe"))}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
    mocker.patch("email_processor.handler.deactivate_player")
    mocker.patch("email_processor.handler.get_active_admins", return_value=[])
    mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"] == "Unsubscribed"


@pytest.mark.unit
def test_handler_unsubscribe_unknown_sender(mocker):
    """UNSUBSCRIBE from an unregistered address returns 403 and sends error reply."""
    import io
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(_make_unsubscribe_email("nobody@example.com"))}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="unknown")
    mock_deactivate = mocker.patch("email_processor.handler.deactivate_player")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 403
    mock_deactivate.assert_not_called()
    mock_send.assert_called_once()
    assert "active player account" in mock_send.call_args[0][2].lower()


@pytest.mark.unit
def test_handler_unsubscribe_guest_sender(mocker):
    """UNSUBSCRIBE from a guest address returns 403."""
    import io
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(_make_unsubscribe_email("guest@example.com"))}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="guest")
    mock_deactivate = mocker.patch("email_processor.handler.deactivate_player")
    mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 403
    mock_deactivate.assert_not_called()


@pytest.mark.unit
def test_handler_unsubscribe_already_inactive(mocker):
    """UNSUBSCRIBE when player is already inactive returns 200 with 'already' message."""
    import io
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(_make_unsubscribe_email("alice@example.com"))}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
    mocker.patch("email_processor.handler.deactivate_player", side_effect=ValueError("No active player found"))
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"] == "Already inactive"
    mock_send.assert_called_once()
    assert "already" in mock_send.call_args[0][2].lower()


@pytest.mark.unit
def test_handler_unsubscribe_does_not_call_bedrock(mocker):
    """UNSUBSCRIBE must short-circuit before any Bedrock call."""
    import io
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(_make_unsubscribe_email("alice@example.com"))}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
    mocker.patch("email_processor.handler.deactivate_player")
    mocker.patch("email_processor.handler.get_active_admins", return_value=[])
    mocker.patch("email_processor.handler.send_email")
    mock_bedrock = mocker.patch("email_processor.handler.parse_player_email")

    handler(_make_s3_event(), None)

    mock_bedrock.assert_not_called()


@pytest.mark.unit
def test_handler_unsubscribe_notifies_all_admins(mocker):
    """All active admins in the players table are notified when a player unsubscribes."""
    import io
    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(_make_unsubscribe_email("alice@example.com"))}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
    mocker.patch("email_processor.handler.deactivate_player")
    mocker.patch("email_processor.handler.get_active_admins", return_value=[
        {"email": "admin1@example.com", "name": "Admin One"},
        {"email": "admin2@example.com", "name": "Admin Two"},
    ])
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    sent_to = [c[0][0] for c in mock_send.call_args_list]
    assert "admin1@example.com" in sent_to
    assert "admin2@example.com" in sent_to
    admin_call = next(c for c in mock_send.call_args_list if c[0][0] == "admin1@example.com")
    assert "alice@example.com" in admin_call[0][2]
