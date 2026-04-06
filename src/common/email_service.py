import logging
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


def send_email(to: str, subject: str, body: str) -> None:
    """Send an email via SES."""
    config = _get_config()
    client = _get_ses_client()

    client.send_email(
        Source=config.sender_email,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        },
    )
    logger.info("Sent email to %s: %s", to, subject)


def send_announcement(
    player_email: str,
    player_name: str | None,
    game_date: str,
) -> None:
    """Send game announcement email."""
    config = _get_config()
    greeting = f"Hi {player_name}" if player_name else "Hi"

    subject = f"Basketball Game - {game_date}"
    body = (
        f"{greeting},\n\n"
        f"A basketball game has been scheduled!\n\n"
        f"Date: {game_date} (Saturday)\n"
        f"Time: {config.game_time}\n"
        f"Location: {config.game_location}\n\n"
        f"Please reply to this email to let us know if you can make it.\n"
        f"You can say things like:\n"
        f"  - \"I'm in\" or \"Yes\" to join\n"
        f"  - \"Can't make it\" or \"No\" to decline\n"
        f"  - \"Maybe\" if you're unsure\n"
        f"  - \"I'm bringing 2 guests: John, Jane\" to bring guests\n\n"
        f"We need at least 6 players to play. Looking forward to it!\n"
    )

    send_email(player_email, subject, body)


def send_reminder(
    player_email: str,
    player_name: str | None,
    confirmed_count: int,
    game_date: str,
) -> None:
    """Send reminder email with current confirmed count."""
    greeting = f"Hi {player_name}" if player_name else "Hi"

    subject = f"Reminder: Basketball Game - {game_date}"
    body = (
        f"{greeting},\n\n"
        f"This is a reminder about the basketball game on {game_date}.\n\n"
        f"We currently have {confirmed_count} confirmed player(s) "
        f"(need at least 6).\n\n"
        f"If you haven't responded yet, please reply to let us know "
        f"if you can make it.\n"
    )

    send_email(player_email, subject, body)


def send_cancellation(player_email: str, game_date: str) -> None:
    """Send game cancellation notice."""
    subject = f"Cancelled: Basketball Game - {game_date}"
    body = (
        f"Hi,\n\n"
        f"Unfortunately, the basketball game scheduled for {game_date} "
        f"has been cancelled due to insufficient players "
        f"(fewer than 6 confirmed).\n\n"
        f"See you next week!\n"
    )

    send_email(player_email, subject, body)


def send_confirmation(
    player_email: str,
    game_date: str,
    roster: dict[str, Any],
) -> None:
    """Send final confirmation with roster to confirmed players."""
    config = _get_config()

    subject = f"Confirmed: Basketball Game - {game_date}"

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
        f"Time: {config.game_time}\n"
        f"Location: {config.game_location}\n\n"
        f"Confirmed players:\n{roster_text}\n\n"
        f"See you there!\n"
    )

    send_email(player_email, subject, body)


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

    send_email(sponsor_email, subject, body)
