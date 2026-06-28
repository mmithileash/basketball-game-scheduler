import boto3
import pytest
from moto import mock_aws

from common.email_service import (
    send_admin_cancelled_broadcast,
    send_admin_unclear_notification,
    send_admin_weekly_prompt,
    send_cancellation,
    send_email,
    send_final_confirmation_with_duration,
    send_guest_followup,
    send_no_game_announcement,
    send_no_game_this_week,
    send_rate_limit_notice,
    send_reminder,
    send_tentative_announcement,
)

_TIERED_POLICY = {
    "minPlayers": 6,
    "threshold": 10,
    "longGame": {"startTime": "10:00 AM", "durationHours": 2},
    "shortGame": {"startTime": "11:00 AM", "durationHours": 1},
}

_FIXED_POLICY = {
    "minPlayers": 6,
    "threshold": 10,
    "longGame": {"startTime": "9:00 AM", "durationHours": 2},
    "shortGame": {"startTime": "9:00 AM", "durationHours": 2},
}


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
def test_send_rate_limit_notice(mocker):
    """The courtesy notice tells the sender they hit the weekly limit and to
    contact the organiser directly; it carries no unsubscribe footer."""
    _setup_ses()

    mock_send = mocker.patch("common.email_service.send_email")
    send_rate_limit_notice("player@example.com")

    mock_send.assert_called_once()
    to_addr, subject, body = mock_send.call_args[0]
    assert to_addr == "player@example.com"
    assert "limit" in subject.lower()
    assert "limit" in body.lower()
    assert "admin@example.com" in body  # organiser contact
    assert "unsubscribe" not in body.lower()


@pytest.mark.unit
@mock_aws
def test_send_reminder(mocker):
    """Verify reminder contains confirmed count and policy minimum."""
    _setup_ses()

    mock_send = mocker.patch("common.email_service.send_email")
    send_reminder("player@example.com", "Bob", 4, "2026-03-28", 6)

    mock_send.assert_called_once()
    subject = mock_send.call_args[0][1]
    body = mock_send.call_args[0][2]

    assert "Reminder" in subject
    assert "2026-03-28" in subject
    assert "4 confirmed" in body
    assert "at least 6" in body
    assert "Hi Bob" in body


@pytest.mark.unit
def test_send_reminder_has_condensed_respond_prompt(mocker):
    """Reminder targets the haven't-clearly-responded cohort, so it restates
    in one line how to respond."""
    mock_send = mocker.patch("common.email_service.send_email")
    send_reminder("player@example.com", "Bob", 4, "2026-03-28", 6)
    body = mock_send.call_args[0][2]
    assert "Reply" in body
    assert "Yes" in body and "No" in body and "Maybe" in body


@pytest.mark.unit
@mock_aws
def test_send_cancellation(mocker):
    """Verify cancellation message uses the game's minimum-players figure."""
    _setup_ses()

    mock_send = mocker.patch("common.email_service.send_email")
    send_cancellation("player@example.com", "2026-03-28", 8)

    mock_send.assert_called_once()
    subject = mock_send.call_args[0][1]
    body = mock_send.call_args[0][2]

    assert "Cancelled" in subject
    assert "2026-03-28" in subject
    assert "cancelled" in body.lower()
    assert "fewer than 8" in body


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
def test_unsubscribe_footer_in_tentative_announcement(mocker):
    """send_tentative_announcement body contains the mailto unsubscribe link."""
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_tentative_announcement("player@example.com", "Alice", "2026-04-12", _TIERED_POLICY)
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


_ISSUES_URL = "https://github.com/mmithileash/basketball-game-scheduler/issues"


@pytest.mark.unit
def test_text_to_html_linkifies_https_url():
    """Bare https URLs become clickable anchors in the HTML part."""
    from common.email_service import _text_to_html
    out = _text_to_html(f"Report it at {_ISSUES_URL}")
    assert f'<a href="{_ISSUES_URL}">{_ISSUES_URL}</a>' in out


@pytest.mark.unit
def test_text_to_html_https_link_excludes_trailing_punctuation():
    """A URL ending a sentence keeps the trailing period outside the anchor."""
    from common.email_service import _text_to_html
    out = _text_to_html("Visit https://example.com/path. Thanks!")
    assert '<a href="https://example.com/path">https://example.com/path</a>.' in out


@pytest.mark.unit
def test_text_to_html_still_linkifies_mailto():
    """The existing mailto handling is preserved."""
    from common.email_service import _text_to_html
    out = _text_to_html("mailto:x@y.com?subject=UNSUBSCRIBE")
    assert '<a href="mailto:x@y.com?subject=UNSUBSCRIBE">' in out


@pytest.mark.unit
def test_report_issues_link_in_player_footer(mocker):
    """Player-facing emails invite bug/issue reports via the GitHub repo."""
    mock_send = mocker.patch("common.email_service.send_email")
    send_tentative_announcement("player@example.com", "Alice", "2026-07-11", _TIERED_POLICY)
    body = mock_send.call_args[0][2]
    assert _ISSUES_URL in body


@pytest.mark.unit
def test_report_issues_link_in_admin_weekly_prompt(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_admin_weekly_prompt("admin@example.com", "2026-07-06")
    body = mock_send.call_args[0][2]
    assert _ISSUES_URL in body


@pytest.mark.unit
def test_report_issues_link_in_admin_unclear_notification(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_admin_unclear_notification("admin@example.com", "bob@example.com", "huh??", "2026-07-11")
    body = mock_send.call_args[0][2]
    assert _ISSUES_URL in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_reminder(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_reminder("player@example.com", "Alice", 4, "2026-04-12", 6)
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_cancellation(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_cancellation("player@example.com", "2026-04-12", 6)
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_final_confirmation(mocker):
    _setup_ses()
    roster = {
        "YES": {"players": {"player@example.com": {"name": "Alice"}}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    }
    mock_send = mocker.patch("common.email_service.send_email")
    send_final_confirmation_with_duration("player@example.com", "2026-04-12", roster, "10:00 AM", 2)
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


# ---------------------------------------------------------------------------
# New email templates
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_send_admin_weekly_prompt_sent_to_admin(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_admin_weekly_prompt("admin@example.com", "2026-07-06")
    mock_send.assert_called_once()
    to, subject, body = mock_send.call_args[0]
    assert to == "admin@example.com"
    assert "2026-07-06" in subject
    assert "Tuesday 9PM UTC" in body


@pytest.mark.unit
def test_send_no_game_this_week_no_response(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_no_game_this_week("player@example.com", "Alice", "2026-07-06", "no_response")
    to, subject, body = mock_send.call_args[0]
    assert to == "player@example.com"
    assert "No Games" in subject
    assert "Hi Alice" in body
    assert "No games were scheduled" in body


@pytest.mark.unit
def test_send_no_game_this_week_admin_declined(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_no_game_this_week("player@example.com", None, "2026-07-06", "admin_declined")
    _, _, body = mock_send.call_args[0]
    assert "organiser has confirmed" in body
    assert "Hi None" not in body


@pytest.mark.unit
def test_send_tentative_announcement_tiered_shows_both_branches(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_tentative_announcement("player@example.com", "Alice", "2026-07-07", _TIERED_POLICY)
    to, subject, body = mock_send.call_args[0]
    assert "[Game: 2026-07-07]" in subject
    assert "10+" in body
    assert "10:00 AM" in body
    assert "11:00 AM" in body
    assert "at least 6" in body
    assert "Hi Alice" in body


def _assert_how_to_respond_block(body: str):
    """Shared assertions: the announcement teaches email RSVP self-sufficiently."""
    facts, _, how_to = body.partition("HOW TO RESPOND")
    assert how_to, "announcement must contain a HOW TO RESPOND section"
    # Facts zone leads
    assert "Date:" in facts
    assert "Location:" in facts
    # Teaching block
    assert "Reply" in how_to
    assert "Yes" in how_to
    assert "No" in how_to
    assert "Maybe" in how_to
    # Change-your-mind reassurance (latest answer wins)
    assert "latest" in how_to.lower() or "change your mind" in how_to.lower()
    # Guest format with the email-optional reason
    assert "guest" in how_to.lower()
    assert "email" in how_to.lower()
    # Roster-query line
    assert "who's playing" in how_to.lower()


@pytest.mark.unit
def test_send_tentative_announcement_tiered_has_how_to_respond_block(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_tentative_announcement("player@example.com", "Alice", "2026-07-07", _TIERED_POLICY)
    _, _, body = mock_send.call_args[0]
    _assert_how_to_respond_block(body)


@pytest.mark.unit
def test_send_tentative_announcement_fixed_has_how_to_respond_block(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_tentative_announcement("player@example.com", "Alice", "2026-07-07", _FIXED_POLICY)
    _, _, body = mock_send.call_args[0]
    _assert_how_to_respond_block(body)


@pytest.mark.unit
def test_send_tentative_announcement_fixed_shows_single_line(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_tentative_announcement("player@example.com", "Alice", "2026-07-07", _FIXED_POLICY)
    _, _, body = mock_send.call_args[0]
    assert "9:00 AM" in body
    assert "10+" not in body
    assert "depend" not in body.lower()


@pytest.mark.unit
def test_send_final_confirmation_with_duration_1hr(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    roster = {
        "YES": {"players": {"alice@example.com": {"name": "Alice"}}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    }
    send_final_confirmation_with_duration("alice@example.com", "2026-07-07", roster, "11:00 AM", 1)
    _, subject, body = mock_send.call_args[0]
    assert "[Game: 2026-07-07]" in subject
    assert "1 hour" in body
    assert "11:00 AM" in body
    assert "Alice" in body


@pytest.mark.unit
def test_send_final_confirmation_with_duration_2hr(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    roster = {"YES": {"players": {}, "guests": []}, "NO": {"players": {}, "guests": []}, "MAYBE": {"players": {}, "guests": []}}
    send_final_confirmation_with_duration("player@example.com", "2026-07-07", roster, "10:00 AM", 2)
    _, _, body = mock_send.call_args[0]
    assert "2 hours" in body
    assert "10:00 AM" in body


@pytest.mark.unit
def test_game_marker_in_tentative_announcement_subject(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_tentative_announcement("player@example.com", "Alice", "2026-07-07", _TIERED_POLICY)
    _, subject, _ = mock_send.call_args[0]
    assert "[Game: 2026-07-07]" in subject


@pytest.mark.unit
def test_game_marker_in_reminder_subject(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_reminder("player@example.com", "Alice", 3, "2026-07-07", 6)
    _, subject, _ = mock_send.call_args[0]
    assert "[Game: 2026-07-07]" in subject


@pytest.mark.unit
def test_game_marker_in_cancellation_subject(mocker):
    mock_send = mocker.patch("common.email_service.send_email")
    send_cancellation("player@example.com", "2026-07-07", 6)
    _, subject, _ = mock_send.call_args[0]
    assert "[Game: 2026-07-07]" in subject


@pytest.mark.unit
def test_send_admin_unclear_notification_contains_player_and_raw_message(mocker):
    """Organiser notification surfaces who sent it and the verbatim message."""
    mock_send = mocker.patch("common.email_service.send_email")
    send_admin_unclear_notification(
        "admin@example.com",
        "bob@example.com",
        "yo can i maybe swing by idk lol",
        "2026-07-07",
    )
    to, subject, body = mock_send.call_args[0]
    assert to == "admin@example.com"
    assert "bob@example.com" in body
    assert "yo can i maybe swing by idk lol" in body
