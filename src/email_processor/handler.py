import email
import logging
from email import policy
from typing import Any

import boto3

from common.bedrock_client import parse_player_email
from common.config import load_config
from common.dynamo import (
    add_guests_to_game_status,
    create_guest_entry,
    delete_guest_entries,
    get_player_name,
    get_roster,
    get_upcoming_game,
    move_confirmed_guests,
    remove_sponsor_guests_from_status,
    update_player_response,
)
from common.email_service import send_email, send_guest_followup

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
    for status, data in roster.items():
        if sender_email in data.get("players", {}):
            return status
    return None


def _format_intent_summary(intent: str, guests: list[dict], confirmed_names: list[str] | None = None) -> str:
    """Return a human-readable summary of what the system understood."""
    guest_names = [g["name"] for g in guests] if guests else []
    confirmed = confirmed_names or []
    summaries = {
        "JOIN": "We've marked you as playing.",
        "DECLINE": "We've marked you as not playing.",
        "MAYBE": "We've marked you as maybe.",
        "BRING_GUESTS": f"We've marked you as playing with guest(s): {', '.join(guest_names)}.",
        "UPDATE_GUESTS": f"We've updated your guest list to: {', '.join(guest_names)}.",
        "QUERY_ROSTER": "You asked about the current roster.",
        "QUERY_PLAYER": "You asked about a player's status.",
        "GUEST_CONFIRM": (
            f"We've confirmed guest(s) still attending: {', '.join(confirmed)}."
            if confirmed
            else "We've noted your message about your guests."
        ),
        "GUEST_DECLINE": "We've noted that your guests won't be attending.",
    }
    return summaries.get(intent, "We weren't sure what you meant.")


def _format_roster_summary(roster: dict[str, Any]) -> str:
    """Format current roster into a readable summary for reply emails."""
    sections = []

    for status, label in [("YES", "Playing"), ("NO", "Not Playing"), ("MAYBE", "Maybe")]:
        data = roster.get(status, {})
        players = data.get("players", {})
        guests = data.get("guests", [])
        if players or guests:
            lines = []
            for player_email, pdata in players.items():
                name = pdata.get("name") or player_email
                lines.append(f"  - {name} ({player_email})")
            for guest in guests:
                lines.append(f"  + Guest: {guest['name']} (via {guest['sponsorName']})")
            sections.append(f"{label} ({len(players)} players, {len(guests)} guests):\n" + "\n".join(lines))

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

    # Look up the upcoming Saturday's game (regardless of status). We need
    # the raw status here so we can distinguish "no game scheduled at all"
    # from "the game was cancelled" — those should produce different replies.
    upcoming_game = get_upcoming_game()
    upcoming_status = upcoming_game.get("status") if upcoming_game else None

    if upcoming_status == "CANCELLED":
        logger.info(
            "Upcoming game %s is CANCELLED, replying to %s without RSVP processing",
            upcoming_game["gameDate"], sender_email,
        )
        send_email(
            sender_email,
            "Re: " + subject,
            f"The game on {upcoming_game['gameDate']} has been cancelled. "
            "A new game will be announced on Monday!",
        )
        return {"statusCode": 200, "body": "Game cancelled"}

    if upcoming_status != "OPEN":
        logger.warning(
            "No open game found (status=%s), ignoring email from %s",
            upcoming_status, sender_email,
        )
        send_email(
            sender_email,
            "Re: " + subject,
            "There is no game currently scheduled. "
            "A new game will be announced on Monday!",
        )
        return {"statusCode": 200, "body": "No open game"}

    game_date = upcoming_game["gameDate"]

    # Get current roster and find player's current status
    roster = get_roster(game_date)
    old_status = _find_player_status(sender_email, roster)

    # Parse intent using Bedrock
    parsed = parse_player_email(body, sender_email, roster)
    intent = parsed["intent"]
    reply_draft = parsed.get("reply_draft", "Thanks for your reply!")

    logger.info("Intent for %s: %s (old_status: %s)", sender_email, intent, old_status)

    # Process based on intent
    if intent == "JOIN":
        player_name = get_player_name(sender_email)
        update_player_response(game_date, sender_email, "YES", name=player_name, old_status=old_status)
    elif intent == "DECLINE":
        player_name = get_player_name(sender_email)
        update_player_response(game_date, sender_email, "NO", name=player_name, old_status=old_status)
        # Move any guests this player brought from YES to NO
        sponsor_guests = remove_sponsor_guests_from_status(game_date, "YES", sender_email)
        if sponsor_guests:
            add_guests_to_game_status(game_date, "NO", sponsor_guests)
            guest_names = [g["name"] for g in sponsor_guests]
            send_guest_followup(
                sponsor_email=sender_email,
                sponsor_name=player_name,
                guest_names=guest_names,
                game_date=game_date,
            )
    elif intent == "MAYBE":
        player_name = get_player_name(sender_email)
        update_player_response(game_date, sender_email, "MAYBE", name=player_name, old_status=old_status)
    elif intent == "BRING_GUESTS":
        player_name = get_player_name(sender_email)
        update_player_response(game_date, sender_email, "YES", name=player_name, old_status=old_status)
        guest_objects = [
            create_guest_entry(
                game_date,
                g["name"],
                sender_email,
                player_name or sender_email,
                g.get("contact_email"),
            )
            for g in parsed.get("guests", [])
        ]
        if guest_objects:
            add_guests_to_game_status(game_date, "YES", guest_objects)
    elif intent == "UPDATE_GUESTS":
        player_name = get_player_name(sender_email)
        old_guest_objects = remove_sponsor_guests_from_status(game_date, "YES", sender_email)
        if old_guest_objects:
            delete_guest_entries(old_guest_objects)
        new_guest_objects = [
            create_guest_entry(
                game_date,
                g["name"],
                sender_email,
                player_name or sender_email,
                g.get("contact_email"),
            )
            for g in parsed.get("guests", [])
        ]
        if new_guest_objects:
            add_guests_to_game_status(game_date, "YES", new_guest_objects)
    elif intent == "GUEST_CONFIRM":
        confirmed_names = parsed.get("confirmed_guest_names", [])
        if confirmed_names:
            move_confirmed_guests(game_date, sender_email, confirmed_names)
        else:
            logger.warning(f"GUEST_CONFIRM from {sender_email} but no confirmed_guest_names in parsed result")
    elif intent == "GUEST_DECLINE":
        # Guests remain in NO — no action needed; game_finalizer will clean up
        logger.info(f"GUEST_DECLINE from {sender_email} — guests remain in NO")
    elif intent in ("QUERY_ROSTER", "QUERY_PLAYER"):
        # No DB update needed; reply_draft from Bedrock contains the answer
        pass
    else:
        logger.warning("Unknown intent: %s", intent)

    # Build full reply with intent summary and roster
    intent_summary = _format_intent_summary(
        intent,
        parsed.get("guests", []),
        parsed.get("confirmed_guest_names", []),
    )
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
