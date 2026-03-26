import json
import logging
from typing import Any

import boto3

from common.config import load_config

logger = logging.getLogger(__name__)

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
        players = roster.get(status, {})
        if players:
            lines.append(f"{status}:")
            for email, data in players.items():
                guests = data.get("guests", [])
                if guests:
                    lines.append(f"  - {email} (+ guests: {', '.join(guests)})")
                else:
                    lines.append(f"  - {email}")
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
        "intent": str,  # JOIN, DECLINE, MAYBE, BRING_GUESTS, QUERY_ROSTER, QUERY_PLAYER, UPDATE_GUESTS
        "guest_count": int,
        "guest_names": list[str],
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
        "(e.g., 'I'm in, bringing 2 friends: John, Jane')\n"
        "- QUERY_ROSTER: Player wants to know the full roster/status\n"
        "- QUERY_PLAYER: Player asks about a specific person\n"
        "- UPDATE_GUESTS: Player already confirmed YES but wants to update their guest list\n\n"
        "Respond with ONLY a JSON object (no markdown, no explanation):\n"
        '{{\n'
        '  "intent": "...",\n'
        '  "guest_count": 0,\n'
        '  "guest_names": [],\n'
        '  "query_target": null,\n'
        '  "reply_draft": "A friendly reply to send back to the player"\n'
        '}}\n\n'
        "For BRING_GUESTS or UPDATE_GUESTS, extract guest names and count.\n"
        "For QUERY_PLAYER, set query_target to the email or name being asked about.\n"
        "The reply_draft should be a brief, friendly response acknowledging "
        "their message and confirming what action was taken."
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

        # Ensure all expected fields are present with defaults
        parsed: dict[str, Any] = {
            "intent": result.get("intent", "MAYBE"),
            "guest_count": result.get("guest_count", 0),
            "guest_names": result.get("guest_names", []),
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
            "guest_count": 0,
            "guest_names": [],
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
            "guest_count": 0,
            "guest_names": [],
            "query_target": None,
            "reply_draft": (
                "Thanks for your reply! I had some trouble processing your "
                "message. I've marked you as 'maybe' for now. "
                "Please reply again or contact the organizer directly."
            ),
        }
