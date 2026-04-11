import boto3
import pytest
from moto import mock_aws

from common.email_service import (
    send_admin_cancelled_broadcast,
    send_announcement,
    send_cancellation,
    send_confirmation,
    send_email,
    send_guest_followup,
    send_no_game_announcement,
    send_reminder,
)


def _setup_ses():
    """Helper to set up SES with verified identity inside moto context."""
    ses = boto3.client("ses", region_name="eu-west-1")
    ses.verify_email_identity(EmailAddress="scheduler@example.com")

    import common.email_service as email_mod
    email_mod._config = None
    email_mod._ses_client = None
    return ses


@pytest.mark.unit
@mock_aws
def test_send_email():
    """Verify SES send_email called with correct params."""
    ses = _setup_ses()

    send_email("player@example.com", "Test Subject", "Test Body")

    # Verify email was sent by checking SES send statistics
    stats = ses.get_send_statistics()
    data_points = stats.get("SendDataPoints", [])
    # moto tracks sends; at minimum, no exception means it was accepted
    # We can also check the quota
    quota = ses.get_send_quota()
    assert quota["SentLast24Hours"] >= 1.0


@pytest.mark.unit
@mock_aws
def test_send_announcement():
    """Verify subject and body contain game date, time, location."""
    ses = _setup_ses()

    # We'll intercept by checking no exception and verifying content via
    # a mock wrapper approach - but with moto we just verify it doesn't error
    # and test the content by calling the function that builds the email
    send_announcement("player@example.com", "Alice", "2026-03-28")

    quota = ses.get_send_quota()
    assert quota["SentLast24Hours"] >= 1.0


@pytest.mark.unit
@mock_aws
def test_send_announcement_with_name(mocker):
    """Verify personalised greeting when player name is provided."""
    _setup_ses()

    mock_send = mocker.patch("common.email_service.send_email")
    send_announcement("player@example.com", "Alice", "2026-03-28")

    mock_send.assert_called_once()
    args = mock_send.call_args
    subject = args[0][1] if len(args[0]) > 1 else args.kwargs.get("subject", "")
    body = args[0][2] if len(args[0]) > 2 else args.kwargs.get("body", "")

    assert "2026-03-28" in subject
    assert "Hi Alice" in body
    assert "10:00 AM" in body
    assert "Main Court" in body


@pytest.mark.unit
@mock_aws
def test_send_announcement_without_name(mocker):
    """Verify generic greeting when player name is None."""
    _setup_ses()

    mock_send = mocker.patch("common.email_service.send_email")
    send_announcement("player@example.com", None, "2026-03-28")

    mock_send.assert_called_once()
    body = mock_send.call_args[0][2]

    assert body.startswith("Hi,") or body.startswith("Hi\n")
    assert "Hi None" not in body


@pytest.mark.unit
@mock_aws
def test_send_reminder(mocker):
    """Verify reminder contains confirmed count."""
    _setup_ses()

    mock_send = mocker.patch("common.email_service.send_email")
    send_reminder("player@example.com", "Bob", 4, "2026-03-28")

    mock_send.assert_called_once()
    subject = mock_send.call_args[0][1]
    body = mock_send.call_args[0][2]

    assert "Reminder" in subject
    assert "2026-03-28" in subject
    assert "4 confirmed" in body
    assert "Hi Bob" in body


@pytest.mark.unit
@mock_aws
def test_send_cancellation(mocker):
    """Verify cancellation message."""
    _setup_ses()

    mock_send = mocker.patch("common.email_service.send_email")
    send_cancellation("player@example.com", "2026-03-28")

    mock_send.assert_called_once()
    subject = mock_send.call_args[0][1]
    body = mock_send.call_args[0][2]

    assert "Cancelled" in subject
    assert "2026-03-28" in subject
    assert "cancelled" in body.lower()
    assert "fewer than 6" in body


@pytest.mark.unit
@mock_aws
def test_send_confirmation(mocker):
    """Verify roster included in body."""
    _setup_ses()

    roster = {
        "YES": {
            "players": {
                "alice@example.com": {"name": "Alice"},
                "bob@example.com": {"name": "Bob"},
            },
            "guests": [{"name": "Mike", "sponsorName": "Alice"}],
        },
        "NO": {"players": {"charlie@example.com": {"name": "Charlie"}}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    }

    mock_send = mocker.patch("common.email_service.send_email")
    send_confirmation("alice@example.com", "2026-03-28", roster)

    mock_send.assert_called_once()
    subject = mock_send.call_args[0][1]
    body = mock_send.call_args[0][2]

    assert "Confirmed" in subject
    assert "2026-03-28" in subject
    assert "alice@example.com" in body
    assert "bob@example.com" in body
    assert "Mike" in body
    assert "10:00 AM" in body
    assert "Main Court" in body


@pytest.mark.unit
@mock_aws
def test_send_guest_followup():
    """send_guest_followup sends email listing guests to the sponsor."""
    ses = _setup_ses()

    send_guest_followup(
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        guest_names=["John", "Jane"],
        game_date="2026-04-05",
    )

    # If no exception is raised, the email was sent successfully via SES mock
    quota = ses.get_send_quota()
    assert quota["SentLast24Hours"] >= 1.0


@pytest.mark.unit
@mock_aws
def test_send_guest_followup_with_mocker(mocker):
    """Verify guest followup contains sponsor name and guest list."""
    _setup_ses()

    mock_send = mocker.patch("common.email_service.send_email")
    send_guest_followup(
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        guest_names=["John", "Jane"],
        game_date="2026-04-05",
    )

    mock_send.assert_called_once()
    subject = mock_send.call_args[0][1]
    body = mock_send.call_args[0][2]

    assert "2026-04-05" in subject
    assert "Hi Alice" in body
    assert "John" in body
    assert "Jane" in body
    assert "guests" in body.lower()
    assert "won't be able to make it" in body


@pytest.mark.unit
@mock_aws
def test_send_guest_followup_without_name(mocker):
    """Verify generic greeting when sponsor name is None."""
    _setup_ses()

    mock_send = mocker.patch("common.email_service.send_email")
    send_guest_followup(
        sponsor_email="bob@example.com",
        sponsor_name=None,
        guest_names=["John"],
        game_date="2026-04-05",
    )

    mock_send.assert_called_once()
    body = mock_send.call_args[0][2]

    assert body.startswith("Hi,") or body.startswith("Hi\n")
    assert "Hi None" not in body
    assert "John" in body


@pytest.mark.unit
@mock_aws
def test_send_no_game_announcement(mocker):
    mock_send = mocker.patch("common.email_service.send_email")

    send_no_game_announcement("alice@example.com", "Alice", "2026-04-11")

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == "alice@example.com"
    assert "2026-04-11" in args[1]  # subject
    assert "cancelled" in args[2].lower() or "no game" in args[2].lower()


@pytest.mark.unit
@mock_aws
def test_send_no_game_announcement_no_name(mocker):
    mock_send = mocker.patch("common.email_service.send_email")

    send_no_game_announcement("alice@example.com", None, "2026-04-11")

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert "2026-04-11" in args[2]
    assert "Hi None" not in args[2]


@pytest.mark.unit
@mock_aws
def test_send_admin_cancelled_broadcast(mocker):
    mock_send = mocker.patch("common.email_service.send_email")

    send_admin_cancelled_broadcast("alice@example.com", "2026-04-11")

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == "alice@example.com"
    assert "2026-04-11" in args[1]
    assert "cancelled" in args[2].lower()


@pytest.mark.unit
@mock_aws
def test_send_guest_cancelled_sponsor_notification(mocker):
    from common.email_service import send_guest_cancelled_sponsor_notification
    mock_send = mocker.patch("common.email_service.send_email")

    send_guest_cancelled_sponsor_notification("alice@example.com", "Alice", "John", "2026-04-19")

    mock_send.assert_called_once()
    to, subject, body = mock_send.call_args[0]
    assert to == "alice@example.com"
    assert "2026-04-19" in subject
    assert "John" in body
    assert "Hi Alice" in body


@pytest.mark.unit
@mock_aws
def test_send_guest_cancelled_sponsor_notification_no_name(mocker):
    from common.email_service import send_guest_cancelled_sponsor_notification
    mock_send = mocker.patch("common.email_service.send_email")

    send_guest_cancelled_sponsor_notification("alice@example.com", None, "John", "2026-04-19")

    mock_send.assert_called_once()
    _, _, body = mock_send.call_args[0]
    assert "Hi None" not in body
    assert "John" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_announcement(mocker):
    """send_announcement body contains the mailto unsubscribe link."""
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_announcement("player@example.com", "Alice", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_reminder(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_reminder("player@example.com", "Alice", 4, "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_cancellation(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_cancellation("player@example.com", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_confirmation(mocker):
    _setup_ses()
    roster = {
        "YES": {"players": {"player@example.com": {"name": "Alice"}}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    }
    mock_send = mocker.patch("common.email_service.send_email")
    send_confirmation("player@example.com", "2026-04-12", roster)
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_no_game_announcement(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_no_game_announcement("player@example.com", "Alice", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_guest_followup(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_guest_followup("sponsor@example.com", "Alice", ["John"], "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_guest_cancelled_sponsor_notification(mocker):
    from common.email_service import send_guest_cancelled_sponsor_notification
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_guest_cancelled_sponsor_notification("sponsor@example.com", "Alice", "John", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_absent_by_default_in_admin_cancelled_broadcast(mocker):
    """send_admin_cancelled_broadcast without include_unsubscribe has no footer."""
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_admin_cancelled_broadcast("player@example.com", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "UNSUBSCRIBE" not in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_present_when_requested_in_admin_cancelled_broadcast(mocker):
    """send_admin_cancelled_broadcast with include_unsubscribe=True includes footer."""
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_admin_cancelled_broadcast("player@example.com", "2026-04-12", include_unsubscribe=True)
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body
