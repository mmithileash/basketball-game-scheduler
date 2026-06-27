import html
import logging
import re
from typing import Any

import boto3

from common.config import load_config

logger = logging.getLogger(__name__)

_config = None
_ses_client = None


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_ses_client():
    global _ses_client
    if _ses_client is None:
        _ses_client = boto3.client("ses")
    return _ses_client


def _unsubscribe_footer() -> str:
    """Return a plain-text unsubscribe block to append to player emails."""
    config = _get_config()
    return (
        f"\n\n---\n"
        f"To unsubscribe from future game emails, click the link below "
        f"(this will open your email client with a pre-filled message):\n"
        f"mailto:{config.sender_email}?subject=UNSUBSCRIBE\n"
    )


def _text_to_html(text: str) -> str:
    """Convert a plain-text email body to basic HTML, making mailto: links clickable."""
    escaped = html.escape(text)
    escaped = re.sub(r"(mailto:[^\s]+)", r'<a href="\1">Unsubscribe</a>', escaped)
    escaped = escaped.replace("\n", "<br>\n")
    return f"<html><body>{escaped}</body></html>"


def send_email(to: str, subject: str, body: str) -> None:
    """Send an email via SES with both plain-text and HTML parts."""
    config = _get_config()
    client = _get_ses_client()

    client.send_email(
        Source=config.sender_email,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": body, "Charset": "UTF-8"},
                "Html": {"Data": _text_to_html(body), "Charset": "UTF-8"},
            },
        },
    )
    logger.info(f"Sent email to {to}: {subject}")


def send_reminder(
    player_email: str,
    player_name: str | None,
    confirmed_count: int,
    game_date: str,
    min_players: int,
) -> None:
    """Send reminder email with current confirmed count and the game's minimum."""
    greeting = f"Hi {player_name}" if player_name else "Hi"

    subject = f"Reminder: Basketball Game - {game_date} [Game: {game_date}]"
    body = (
        f"{greeting},\n\n"
        f"This is a reminder about the basketball game on {game_date}.\n\n"
        f"We currently have {confirmed_count} confirmed player(s) "
        f"(need at least {min_players}).\n\n"
        f"If you haven't responded yet, please reply to let us know "
        f"if you can make it.\n"
    )

    send_email(player_email, subject, body + _unsubscribe_footer())


def send_cancellation(player_email: str, game_date: str, min_players: int) -> None:
    """Send game cancellation notice citing the game's minimum-players figure."""
    subject = f"Cancelled: Basketball Game - {game_date} [Game: {game_date}]"
    body = (
        f"Hi,\n\n"
        f"Unfortunately, the basketball game scheduled for {game_date} "
        f"has been cancelled due to insufficient players "
        f"(fewer than {min_players} confirmed).\n\n"
        f"See you next week!\n"
    )

    send_email(player_email, subject, body + _unsubscribe_footer())


def send_guest_followup(
    sponsor_email: str,
    sponsor_name: str | None,
    guest_names: list[str],
    game_date: str,
) -> None:
    """Ask the sponsor whether their guests are still attending after they declined."""
    greeting = f"Hi {sponsor_name}" if sponsor_name else "Hi"
    guest_list = ", ".join(guest_names)

    subject = f"Your guests for the basketball game on {game_date}"
    body = (
        f"{greeting},\n\n"
        f"We noticed you won't be able to make it to the basketball game on {game_date}. "
        f"You had listed the following guest(s): {guest_list}.\n\n"
        f"Are any of them still planning to attend?\n\n"
        f"Please reply with the names of guests who are still coming, and optionally "
        f"a contact email for each (e.g. 'John - john@example.com, Jane').\n\n"
        f"If no reply is received before Friday's cutoff, we'll assume they won't attend.\n"
    )

    send_email(sponsor_email, subject, body + _unsubscribe_footer())


def send_no_game_announcement(
    player_email: str,
    player_name: str | None,
    game_date: str,
) -> None:
    """Notify a player that no game is scheduled this week (admin pre-cancelled)."""
    greeting = f"Hi {player_name}" if player_name else "Hi"

    subject = f"No Game This Week - {game_date}"
    body = (
        f"{greeting},\n\n"
        f"There will be no basketball game this week ({game_date}). "
        f"The game has been cancelled by the organiser.\n\n"
        f"See you next week!\n"
    )

    send_email(player_email, subject, body + _unsubscribe_footer())


def send_guest_cancelled_sponsor_notification(
    sponsor_email: str,
    sponsor_name: str | None,
    guest_name: str,
    game_date: str,
) -> None:
    """Notify a sponsor that their guest has cancelled their attendance."""
    greeting = f"Hi {sponsor_name}" if sponsor_name else "Hi"

    subject = f"Your guest cancelled: Basketball Game - {game_date}"
    body = (
        f"{greeting},\n\n"
        f"{guest_name} has cancelled their attendance for the basketball game on {game_date}.\n\n"
        f"If you'd like to bring another guest instead, just reply to the original announcement.\n"
    )

    send_email(sponsor_email, subject, body + _unsubscribe_footer())


def send_admin_weekly_prompt(admin_email: str, week_start_date: str) -> None:
    """Ask the admin whether to schedule games for the upcoming week."""
    subject = f"Schedule games for week of {week_start_date}?"
    body = (
        f"Hi,\n\n"
        f"Would you like to schedule any basketball games for the week of {week_start_date}?\n\n"
        f"Reply with the dates and optionally times "
        f"(e.g. 'Tuesday and Saturday' or 'Thursday 7PM'). "
        f"If no time is given, the default is 11:00 AM UTC.\n\n"
        f"To skip this week, reply 'No games this week'.\n\n"
        f"Please reply by Tuesday 9PM UTC.\n"
    )
    send_email(admin_email, subject, body)


def send_no_game_this_week(
    player_email: str,
    player_name: str | None,
    week_start_date: str,
    reason: str,
) -> None:
    """Notify a player that no games are scheduled for the given week."""
    greeting = f"Hi {player_name}" if player_name else "Hi"
    subject = f"No Games This Week ({week_start_date})"
    if reason == "admin_declined":
        detail = "The organiser has confirmed there are no games scheduled this week."
    else:
        detail = "No games were scheduled for this week."
    body = (
        f"{greeting},\n\n"
        f"{detail}\n\n"
        f"See you next week!\n"
    )
    send_email(player_email, subject, body + _unsubscribe_footer())


def _duration_label(hours: int) -> str:
    return f"{hours} hour{'s' if hours > 1 else ''}"


def send_tentative_announcement(
    player_email: str,
    player_name: str | None,
    game_date: str,
    policy: dict[str, Any],
) -> None:
    """Send game announcement driven by the game's policy.

    When the two tiers differ the email shows both turnout-dependent branches
    with concrete times; when they are equal it shows a single time line.
    """
    from common.policy import is_fixed

    config = _get_config()
    greeting = f"Hi {player_name}" if player_name else "Hi"
    subject = f"Basketball Game - {game_date} [Game: {game_date}]"

    long_game = policy["longGame"]
    short_game = policy["shortGame"]

    if is_fixed(policy):
        timing = (
            f"Time: {long_game['startTime']}\n"
            f"Duration: {_duration_label(int(long_game['durationHours']))}\n"
        )
    else:
        timing = (
            f"The start time and duration depend on how many of us sign up:\n"
            f"  - If {policy['threshold']}+ players confirm: "
            f"{long_game['startTime']} for {_duration_label(int(long_game['durationHours']))}\n"
            f"  - Otherwise: "
            f"{short_game['startTime']} for {_duration_label(int(short_game['durationHours']))}\n"
        )

    body = (
        f"{greeting},\n\n"
        f"A basketball game has been scheduled!\n\n"
        f"Date: {game_date}\n"
        f"{timing}"
        f"Location: {config.game_location}\n\n"
        f"Please reply to let us know if you can make it:\n"
        f"  - \"I'm in\" or \"Yes\" to join\n"
        f"  - \"Can't make it\" or \"No\" to decline\n"
        f"  - \"Maybe\" if you're unsure\n"
        f"  - \"I'm bringing 2 guests: John, Jane\" to bring guests\n\n"
        f"We need at least {policy['minPlayers']} players to play.\n"
    )
    send_email(player_email, subject, body + _unsubscribe_footer())


def send_final_confirmation_with_duration(
    player_email: str,
    game_date: str,
    roster: dict[str, Any],
    start_time: str,
    duration_hours: int,
) -> None:
    """Send final game confirmation with the locked-in start time and duration."""
    config = _get_config()
    subject = f"Confirmed: Basketball Game - {game_date} [Game: {game_date}]"

    yes_data = roster.get("YES", {})
    lines: list[str] = []
    for email, data in yes_data.get("players", {}).items():
        name = data.get("name") or email
        lines.append(f"  - {name} ({email})")
    for guest in yes_data.get("guests", []):
        lines.append(f"    + Guest: {guest['name']} (via {guest['sponsorName']})")

    roster_text = "\n".join(lines) if lines else "  (none)"

    body = (
        f"Hi,\n\n"
        f"The basketball game is ON for {game_date}!\n\n"
        f"Time: {start_time}\n"
        f"Duration: {_duration_label(duration_hours)}\n"
        f"Location: {config.game_location}\n\n"
        f"Confirmed players:\n{roster_text}\n\n"
        f"See you there!\n"
    )
    send_email(player_email, subject, body + _unsubscribe_footer())


def send_admin_cancelled_broadcast(player_email: str, game_date: str, include_unsubscribe: bool = False) -> None:
    """Notify a player that an already-announced game has been cancelled by admin."""
    subject = f"Cancelled: Basketball Game - {game_date}"
    body = (
        f"Hi,\n\n"
        f"The basketball game scheduled for {game_date} has been cancelled by the organiser.\n\n"
        f"See you next week!\n"
    )
    if include_unsubscribe:
        body += _unsubscribe_footer()
    send_email(player_email, subject, body)
