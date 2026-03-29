import email
import logging
from email import policy
from typing import Any

import boto3

from common.bedrock_client import parse_player_email
from common.config import load_config
from common.dynamo import (
    get_current_open_game,
    get_roster,
    update_player_response,
)
from common.email_service import send_email

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _extract_email_body(msg: email.message.Message) -> str:
    """Extract plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
        # Fallback: try HTML if no plain text found
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    return ""


def _extract_sender_email(from_header: str) -> str:
    """Extract the email address from a From header value."""
    # Handle formats like "Name <email@example.com>" or just "email@example.com"
    if "<" in from_header and ">" in from_header:
        return from_header.split("<")[1].split(">")[0].strip()
    return from_header.strip()


def _find_player_status(sender_email: str, roster: dict[str, Any]) -> str | None:
    """Find the player's current status in the roster."""
    for status, players in roster.items():
        if sender_email in players:
            return status
    return None


def _format_intent_summary(intent: str, guest_names: list[str]) -> str:
    """Return a human-readable summary of what the system understood."""
    summaries = {
        "JOIN": "We've marked you as playing.",
        "DECLINE": "We've marked you as not playing.",
        "MAYBE": "We've marked you as maybe.",
        "BRING_GUESTS": f"We've marked you as playing with guest(s): {', '.join(guest_names)}.",
        "UPDATE_GUESTS": f"We've updated your guest list to: {', '.join(guest_names)}.",
        "QUERY_ROSTER": "You asked about the current roster.",
        "QUERY_PLAYER": "You asked about a player's status.",
    }
    return summaries.get(intent, "We weren't sure what you meant.")


def _format_roster_summary(roster: dict[str, Any]) -> str:
    """Format current roster into a readable summary for reply emails."""
    sections = []

    for status, label in [("YES", "Playing"), ("NO", "Not Playing"), ("MAYBE", "Maybe")]:
        players = roster.get(status, {})
        if players:
            lines = []
            for player_email, data in players.items():
                lines.append(f"  - {player_email}")
                for guest in data.get("guests", []):
                    lines.append(f"    + Guest: {guest}")
            sections.append(f"{label} ({len(players)}):\n" + "\n".join(lines))

    if not sections:
        return "\n\n---\nNo responses yet."

    return "\n\n---\nCurrent Responses:\n\n" + "\n\n".join(sections)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: process inbound player email."""
    config = load_config()

    # Extract S3 bucket and key from S3 event
    s3_record = event["Records"][0]["s3"]
    bucket = s3_record["bucket"]["name"]
    key = s3_record["object"]["key"]

    logger.info("Processing email from S3: %s/%s", bucket, key)

    # Get raw email from S3
    s3_client = _get_s3_client()
    response = s3_client.get_object(Bucket=bucket, Key=key)
    raw_email = response["Body"].read()

    # Parse the email
    msg = email.message_from_bytes(raw_email, policy=policy.default)
    from_header = msg.get("From", "")
    subject = msg.get("Subject", "")
    body = _extract_email_body(msg)

    sender_email = _extract_sender_email(from_header)
    logger.info("Email from %s, subject: %s", sender_email, subject)

    # Look up current open game
    open_game = get_current_open_game()
    if not open_game:
        logger.warning("No open game found, ignoring email from %s", sender_email)
        send_email(
            sender_email,
            "Re: " + subject,
            "There is no game currently scheduled. "
            "A new game will be announced on Monday!",
        )
        return {"statusCode": 200, "body": "No open game"}

    game_date = open_game["gameDate"]

    # Get current roster and find player's current status
    roster = get_roster(game_date)
    old_status = _find_player_status(sender_email, roster)

    # Parse intent using Bedrock
    parsed = parse_player_email(body, sender_email, roster)
    intent = parsed["intent"]
    guest_names = parsed.get("guest_names", [])
    reply_draft = parsed.get("reply_draft", "Thanks for your reply!")

    logger.info("Intent for %s: %s (old_status: %s)", sender_email, intent, old_status)

    # Process based on intent
    if intent == "JOIN":
        update_player_response(
            game_date, sender_email, "YES", guests=None, old_status=old_status
        )
    elif intent == "DECLINE":
        update_player_response(
            game_date, sender_email, "NO", guests=None, old_status=old_status
        )
    elif intent == "MAYBE":
        update_player_response(
            game_date, sender_email, "MAYBE", guests=None, old_status=old_status
        )
    elif intent == "BRING_GUESTS":
        update_player_response(
            game_date, sender_email, "YES", guests=guest_names, old_status=old_status
        )
    elif intent == "UPDATE_GUESTS":
        update_player_response(
            game_date, sender_email, "YES", guests=guest_names, old_status=old_status
        )
    elif intent in ("QUERY_ROSTER", "QUERY_PLAYER"):
        # No DB update needed; reply_draft from Bedrock contains the answer
        pass
    else:
        logger.warning("Unknown intent: %s", intent)

    # Build full reply with intent summary and roster
    intent_summary = _format_intent_summary(intent, guest_names)
    updated_roster = get_roster(game_date)
    roster_summary = _format_roster_summary(updated_roster)

    full_reply = f"{reply_draft}\n\n{intent_summary}{roster_summary}"
    send_email(sender_email, "Re: " + subject, full_reply)

    return {
        "statusCode": 200,
        "body": {
            "sender": sender_email,
            "intent": intent,
            "gameDate": game_date,
        },
    }
