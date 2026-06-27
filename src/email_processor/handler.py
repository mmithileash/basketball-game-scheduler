import logging
import re
from typing import Any

from common.bedrock_client import parse_player_email
from common.dynamo import (
    add_guests_to_game_status,
    create_guest_entry,
    deactivate_player,
    delete_guest_entries,
    get_active_admins,
    get_game_status,
    get_open_games,
    get_player_name,
    get_roster,
    get_sender_role,
    move_confirmed_guests,
    remove_guest_from_status,
    remove_sponsor_guests_from_status,
    update_player_response,
)
from common.email_utils import fetch_email_from_s3
from common.email_service import (
    send_email,
    send_guest_cancelled_sponsor_notification,
    send_guest_followup,
)

_GAME_DATE_RE = re.compile(r"\[Game:\s*(\d{4}-\d{2}-\d{2})\]")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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


def _handle_unsubscribe(sender_email: str) -> dict[str, Any]:
    """Process a player self-unsubscribe request."""
    role = get_sender_role(sender_email)
    if role != "player":
        logger.warning(f"Unsubscribe attempt from non-player: {sender_email} (role={role})")
        send_email(
            sender_email,
            "Re: UNSUBSCRIBE",
            "We couldn't find an active player account for this email address. "
            "Please contact the organiser if you believe this is an error.",
        )
        return {"statusCode": 403, "body": "Not an active player"}

    try:
        deactivate_player(sender_email)
    except ValueError:
        logger.warning(f"Player {sender_email} attempted self-unsubscribe but is already inactive")
        send_email(
            sender_email,
            "Re: UNSUBSCRIBE",
            "You are already unsubscribed from game announcements. "
            "Please contact the organiser if you'd like to rejoin.",
        )
        return {"statusCode": 200, "body": "Already inactive"}

    send_email(
        sender_email,
        "You've been unsubscribed",
        "You've been successfully unsubscribed from future basketball game announcements.\n\n"
        "If you'd like to rejoin, please contact the organiser.",
    )
    for admin in get_active_admins():
        send_email(
            admin["email"],
            f"Player unsubscribed: {sender_email}",
            f"{sender_email} has unsubscribed from game announcements.\n\n"
            f"To reactivate them, send an admin command: Reactivate {sender_email}",
        )
    logger.info(f"Player {sender_email} self-unsubscribed")
    return {"statusCode": 200, "body": "Unsubscribed"}


def _apply_player_intent(
    game_date: str,
    sender_email: str,
    intent: str,
    parsed: dict[str, Any],
    old_status: str | None,
) -> None:
    """Apply the side effects of a player's intent to DynamoDB."""
    if intent == "JOIN":
        player_name = get_player_name(sender_email)
        update_player_response(game_date, sender_email, "YES", name=player_name, old_status=old_status)
    elif intent == "DECLINE":
        player_name = get_player_name(sender_email)
        update_player_response(game_date, sender_email, "NO", name=player_name, old_status=old_status)
        sponsor_guests = remove_sponsor_guests_from_status(game_date, "YES", sender_email)
        if sponsor_guests:
            add_guests_to_game_status(game_date, "NO", sponsor_guests)
            send_guest_followup(
                sponsor_email=sender_email,
                sponsor_name=player_name,
                guest_names=[g["name"] for g in sponsor_guests],
                game_date=game_date,
            )
    elif intent == "MAYBE":
        player_name = get_player_name(sender_email)
        update_player_response(game_date, sender_email, "MAYBE", name=player_name, old_status=old_status)
    elif intent == "BRING_GUESTS":
        player_name = get_player_name(sender_email)
        update_player_response(game_date, sender_email, "YES", name=player_name, old_status=old_status)
        guest_objects = [
            create_guest_entry(game_date, g["name"], sender_email, player_name or sender_email, g.get("contact_email"))
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
            create_guest_entry(game_date, g["name"], sender_email, player_name or sender_email, g.get("contact_email"))
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
        logger.info(f"GUEST_DECLINE from {sender_email} — guests remain in NO")
    elif intent in ("QUERY_ROSTER", "QUERY_PLAYER"):
        pass
    else:
        logger.warning("Unknown intent: %s", intent)


def _handle_guest_email(
    sender_email: str, subject: str, body: str, game_date: str, roster: dict[str, Any]
) -> dict[str, Any]:
    """Process an inbound email from a confirmed guest."""
    parsed = parse_player_email(body, sender_email, roster)
    intent = parsed["intent"]
    reply_draft = parsed.get("reply_draft", "Thanks for your message!")
    logger.info("Guest intent for %s: %s", sender_email, intent)

    if intent == "DECLINE":
        guest_obj = remove_guest_from_status(game_date, "YES", sender_email)
        if guest_obj:
            add_guests_to_game_status(game_date, "NO", [guest_obj])
            send_guest_cancelled_sponsor_notification(
                guest_obj["sponsorEmail"],
                get_player_name(guest_obj["sponsorEmail"]),
                guest_obj["name"],
                game_date,
            )
    elif intent not in ("QUERY_ROSTER", "QUERY_PLAYER"):
        reply_draft = (
            "As a guest you can only cancel your attendance or ask who's playing. "
            "Please contact the organiser for anything else."
        )

    updated_roster = get_roster(game_date)
    send_email(sender_email, "Re: " + subject, f"{reply_draft}{_format_roster_summary(updated_roster)}")
    return {"statusCode": 200, "body": {"sender": sender_email, "intent": intent, "gameDate": game_date}}


def _handle_player_email(
    sender_email: str, subject: str, body: str, game_date: str, roster: dict[str, Any]
) -> dict[str, Any]:
    """Process an inbound email from a registered player."""
    old_status = _find_player_status(sender_email, roster)
    parsed = parse_player_email(body, sender_email, roster)
    intent = parsed["intent"]
    reply_draft = parsed.get("reply_draft", "Thanks for your reply!")
    logger.info("Intent for %s: %s (old_status: %s)", sender_email, intent, old_status)

    _apply_player_intent(game_date, sender_email, intent, parsed, old_status)

    intent_summary = _format_intent_summary(intent, parsed.get("guests", []), parsed.get("confirmed_guest_names", []))
    updated_roster = get_roster(game_date)
    full_reply = f"{reply_draft}\n\n{intent_summary}{_format_roster_summary(updated_roster)}"
    send_email(sender_email, "Re: " + subject, full_reply)
    return {"statusCode": 200, "body": {"sender": sender_email, "intent": intent, "gameDate": game_date}}


def _resolve_game_date(subject: str, sender_email: str, body: str) -> str | None:
    """Determine which open game a player's email belongs to.

    Resolution order:
    1. [Game: YYYY-MM-DD] marker in the Subject header — if that game is OPEN,
       use it; if CANCELLED, reply with the cancellation notice and stop; if
       missing/PLAYED, fall through to open-games resolution.
    2. Exactly one open game exists → use it unambiguously
    3. Multiple open games → check Bedrock query_target for a date hint
    4. Still ambiguous → send clarification reply and return None
    """
    match = _GAME_DATE_RE.search(subject)
    if match:
        marker_date = match.group(1)
        game = get_game_status(marker_date)
        status = game.get("status") if game else None
        if status == "OPEN":
            logger.info("Resolved game date from subject marker: %s", marker_date)
            return marker_date
        if status == "CANCELLED":
            logger.info("Game %s referenced in subject is CANCELLED", marker_date)
            send_email(
                sender_email,
                f"Re: {subject}",
                f"The game on {marker_date} has been cancelled. "
                f"A new game will be announced soon!",
            )
            return None
        # PLAYED or missing — fall through to open-games resolution below

    open_games = get_open_games()
    if len(open_games) == 0:
        logger.warning("No open games found for email from %s", sender_email)
        send_email(
            sender_email,
            f"Re: {subject}",
            "There is no game currently scheduled. A new game will be announced soon!",
        )
        return None

    if len(open_games) == 1:
        logger.info("Single open game %s, no disambiguation needed", open_games[0]["gameDate"])
        return open_games[0]["gameDate"]

    # Multiple open games — try Bedrock query_target for a date hint
    open_dates = sorted(g["gameDate"] for g in open_games)
    try:
        parsed = parse_player_email(body, sender_email, {})
        query_target = parsed.get("query_target") or ""
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", query_target)
        if date_match and date_match.group(0) in open_dates:
            logger.info("Resolved game date from Bedrock query_target: %s", date_match.group(0))
            return date_match.group(0)
    except Exception:
        logger.warning("Bedrock failed during multi-game disambiguation", exc_info=True)

    # Ambiguous — ask the player to clarify
    date_list = ", ".join(open_dates)
    send_email(
        sender_email,
        f"Re: {subject}",
        f"We have multiple games scheduled ({date_list}). "
        f"Please reply to the specific game announcement email so we know which one you mean.",
    )
    logger.info("Sent disambiguation request to %s (open games: %s)", sender_email, date_list)
    return None


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: process inbound player email."""
    s3_record = event["Records"][0]["s3"]
    bucket = s3_record["bucket"]["name"]
    key = s3_record["object"]["key"]

    logger.info("Processing email from S3: %s/%s", bucket, key)
    sender_email, subject, body = fetch_email_from_s3(bucket, key)
    logger.info("Email from %s, subject: %s", sender_email, subject)

    if subject.strip().upper() == "UNSUBSCRIBE":
        return _handle_unsubscribe(sender_email)

    role = get_sender_role(sender_email)
    if role == "unknown":
        logger.warning("Rejected unregistered sender: %s", sender_email)
        send_email(
            sender_email,
            "Re: " + subject,
            "You are not a registered player. "
            "Please contact the organiser if you believe this is an error.",
        )
        return {"statusCode": 403, "body": "Not a registered player"}

    game_date = _resolve_game_date(subject, sender_email, body)
    if game_date is None:
        return {"statusCode": 200, "body": "No open game or ambiguous"}

    roster = get_roster(game_date)

    if role == "guest":
        return _handle_guest_email(sender_email, subject, body, game_date, roster)

    return _handle_player_email(sender_email, subject, body, game_date, roster)
