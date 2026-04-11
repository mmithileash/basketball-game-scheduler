import json
import logging
from datetime import date
from typing import Any

import boto3

from common.config import load_config
from common.date_utils import next_saturday

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_config = None
_bedrock_client = None


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime")
    return _bedrock_client


def _build_roster_context(roster: dict[str, Any]) -> str:
    """Build a human-readable roster summary for the LLM context."""
    lines: list[str] = []
    for status in ("YES", "NO", "MAYBE"):
        data = roster.get(status, {})
        if isinstance(data, dict):
            players = data.get("players", {})
            guests = data.get("guests", [])
        else:
            # Legacy shape fallback (email-keyed dict)
            players = data
            guests = []

        if players or guests:
            lines.append(f"{status}:")
            for email, pdata in players.items():
                name = pdata.get("name") or email
                lines.append(f"  - {name} ({email})")
            for g in guests:
                lines.append(f"  + Guest: {g['name']} (sponsor: {g.get('sponsorEmail', '')})")
        else:
            lines.append(f"{status}: (none)")
    return "\n".join(lines)


def parse_player_email(
    email_body: str,
    sender_email: str,
    roster: dict[str, Any],
) -> dict[str, Any]:
    """Use Bedrock Claude to parse a player's email reply into a structured intent.

    Returns: {
        "intent": str,  # JOIN, DECLINE, MAYBE, BRING_GUESTS, UPDATE_GUESTS, QUERY_ROSTER, QUERY_PLAYER, GUEST_CONFIRM, GUEST_DECLINE
        "guests": list[{name, contact_email}],
        "confirmed_guest_names": list[str],
        "query_target": str | None,
        "reply_draft": str,
    }
    """
    config = _get_config()
    client = _get_bedrock_client()

    roster_context = _build_roster_context(roster)

    system_prompt = (
        "You are a basketball game scheduler assistant. Your job is to interpret "
        "player email replies and determine their intent.\n\n"
        "The sender's email is: {sender_email}\n\n"
        "Current roster:\n{roster_context}\n\n"
        "Available intents:\n"
        "- JOIN: Player wants to play (e.g., 'I'm in', 'Yes', 'Count me in')\n"
        "- DECLINE: Player cannot play (e.g., 'Can't make it', 'No', 'Out')\n"
        "- MAYBE: Player is uncertain (e.g., 'Maybe', 'Not sure yet')\n"
        "- BRING_GUESTS: Player is joining AND bringing guests "
        "(e.g., 'I'm in, bringing John and Jane')\n"
        "- UPDATE_GUESTS: Player already YES but wants to change their guest list\n"
        "- QUERY_ROSTER: Player wants to know the full roster/status\n"
        "- QUERY_PLAYER: Player asks about a specific person\n"
        "- GUEST_CONFIRM: Player previously declined but is confirming some/all of their "
        "guests are still attending (e.g., 'John is still coming')\n"
        "- GUEST_DECLINE: Player previously declined and their guests are also not coming\n\n"
        "Respond with ONLY a JSON object (no markdown, no explanation):\n"
        '{{\n'
        '  "intent": "...",\n'
        '  "guests": [{{"name": "...", "contact_email": null}}],\n'
        '  "confirmed_guest_names": [],\n'
        '  "query_target": null,\n'
        '  "reply_draft": "A friendly reply to send back to the player"\n'
        '}}\n\n'
        "For BRING_GUESTS or UPDATE_GUESTS: populate 'guests' with each guest's name and "
        "their contact email if provided (null otherwise).\n"
        "For GUEST_CONFIRM: populate 'confirmed_guest_names' with the names of guests "
        "the sponsor confirmed are still attending.\n"
        "For QUERY_PLAYER, set query_target to the email or name being asked about.\n"
        "The reply_draft should be a brief, friendly response confirming the action taken."
    ).format(sender_email=sender_email, roster_context=roster_context)

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": email_body},
        ],
    }

    try:
        response = client.invoke_model(
            modelId=config.bedrock_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())
        assistant_text = response_body["content"][0]["text"].strip()

        # Parse the JSON response
        result = json.loads(assistant_text)

        logger.info(
            f"Result from LLM parsing {sender_email}, {result}"
        )
        # Ensure all expected fields are present with defaults
        parsed: dict[str, Any] = {
            "intent": result.get("intent", "MAYBE"),
            "guests": result.get("guests", []),
            "confirmed_guest_names": result.get("confirmed_guest_names", []),
            "query_target": result.get("query_target"),
            "reply_draft": result.get(
                "reply_draft", "Thanks for your reply! We've noted your response."
            ),
        }

        logger.info(
            "Parsed intent for %s: %s", sender_email, parsed["intent"]
        )
        return parsed

    except json.JSONDecodeError as e:
        logger.error("Failed to parse Bedrock response as JSON: %s", e)
        return {
            "intent": "MAYBE",
            "guests": [],
            "confirmed_guest_names": [],
            "query_target": None,
            "reply_draft": (
                "Thanks for your reply! I had a little trouble understanding "
                "your message. I've marked you as 'maybe' for now. "
                "Please reply again with a clearer response if needed."
            ),
        }
    except Exception as e:
        logger.error("Error calling Bedrock: %s", e, exc_info=True)
        return {
            "intent": "MAYBE",
            "guests": [],
            "confirmed_guest_names": [],
            "query_target": None,
            "reply_draft": (
                "Thanks for your reply! I had some trouble processing your "
                "message. I've marked you as 'maybe' for now. "
                "Please reply again or contact the organizer directly."
            ),
        }


def parse_admin_email(email_body: str, sender_email: str) -> dict[str, Any]:
    """Use Bedrock Claude to parse an admin command email into a structured intent.

    Returns: {
        "intent": str,  # CANCEL_GAME, ADD_PLAYER, ADD_ADMIN, DEACTIVATE_PLAYER, REACTIVATE_PLAYER, UNKNOWN
        "game_date": str | None,   # YYYY-MM-DD Saturday, for CANCEL_GAME
        "email": str | None,       # Target player email, for player management commands
        "name": str | None,        # Player name, for ADD_PLAYER / ADD_ADMIN
        "is_admin": bool | None,   # True for ADD_ADMIN
    }
    """
    config = _get_config()
    client = _get_bedrock_client()

    today = date.today()
    upcoming_saturday = next_saturday()

    system_prompt = (
        "You are an admin command parser for a basketball game scheduling system. "
        "Parse the admin's email and return a structured JSON command.\n\n"
        f"Admin sender: {sender_email}\n"
        f"Today's date: {today.isoformat()} ({today.strftime('%A')})\n"
        f"Next upcoming Saturday (the next scheduled game): {upcoming_saturday.isoformat()}\n\n"
        "Available intents:\n"
        "- CANCEL_GAME: Admin wants to cancel a game for a specific Saturday\n"
        "- ADD_PLAYER: Admin wants to add a new regular player\n"
        "- ADD_ADMIN: Admin wants to add a new admin player\n"
        "- DEACTIVATE_PLAYER: Admin wants to deactivate/remove a player\n"
        "- REACTIVATE_PLAYER: Admin wants to reactivate a previously inactive player\n"
        "- UNKNOWN: Command not understood\n\n"
        "Respond with ONLY a JSON object (no markdown, no explanation):\n"
        '{{\n'
        '  "intent": "...",\n'
        '  "game_date": "YYYY-MM-DD or null — must be a Saturday, resolve to nearest Saturday if needed",\n'
        '  "email": "player email or null",\n'
        '  "name": "player name or null",\n'
        '  "is_admin": true/false/null\n'
        '}}\n\n'
        "For CANCEL_GAME: set game_date to the Saturday being cancelled (YYYY-MM-DD). "
        "If the date mentioned is not a Saturday, resolve it to that week's Saturday. "
        f"If the admin uses a relative reference like 'latest', 'upcoming', 'next', or 'this Saturday', "
        f"resolve it to the next upcoming Saturday ({upcoming_saturday.isoformat()}). "
        "If you truly cannot determine the date, set game_date to null.\n"
        "For ADD_ADMIN: set is_admin to true.\n"
        "For ADD_PLAYER: set is_admin to false.\n"
        "For DEACTIVATE_PLAYER / REACTIVATE_PLAYER: set email to the player's email address."
    )

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 256,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": email_body},
        ],
    }

    try:
        response = client.invoke_model(
            modelId=config.bedrock_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())
        assistant_text = response_body["content"][0]["text"].strip()
        result = json.loads(assistant_text)

        logger.info(f"Admin command parsed for {sender_email}: {result}")

        return {
            "intent": result.get("intent", "UNKNOWN"),
            "game_date": result.get("game_date"),
            "email": result.get("email"),
            "name": result.get("name"),
            "is_admin": result.get("is_admin"),
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Bedrock admin response as JSON: {e}")
        return {
            "intent": "UNKNOWN",
            "game_date": None,
            "email": None,
            "name": None,
            "is_admin": None,
        }
    except Exception as e:
        logger.error(f"Error calling Bedrock for admin command: {e}", exc_info=True)
        return {
            "intent": "UNKNOWN",
            "game_date": None,
            "email": None,
            "name": None,
            "is_admin": None,
        }
