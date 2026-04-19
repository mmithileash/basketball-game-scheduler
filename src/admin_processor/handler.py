import logging
from typing import Any

from common.bedrock_client import parse_admin_email
from common.dynamo import (
    add_player,
    deactivate_player,
    get_game_status,
    get_roster,
    is_admin,
    pre_cancel_game,
    reactivate_player,
    update_game_status,
)
from common.email_service import send_admin_cancelled_broadcast, send_email
from common.email_utils import fetch_email_from_s3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: process admin command emails."""
    s3_record = event["Records"][0]["s3"]
    bucket = s3_record["bucket"]["name"]
    key = s3_record["object"]["key"]

    logger.info(f"Processing admin email from S3: {bucket}/{key}")

    sender_email, subject, body = fetch_email_from_s3(bucket, key)
    if not subject:
        subject = "Admin Command"
    logger.info(f"Admin email from {sender_email}, subject: {subject}")

    if not is_admin(sender_email):
        logger.warning(f"Rejected non-admin sender: {sender_email}")
        send_email(
            sender_email,
            f"Re: {subject}",
            "You are not authorised to send admin commands. "
            "Please contact the organiser if you believe this is an error.",
        )
        return {"statusCode": 403, "body": "Not authorised"}

    parsed = parse_admin_email(body, sender_email)
    intent = parsed["intent"]

    logger.info(f"Admin intent from {sender_email}: {intent}")

    if intent == "CANCEL_GAME":
        game_date = parsed.get("game_date")
        if not game_date:
            send_email(
                sender_email,
                f"Re: {subject}",
                "I couldn't determine which date to cancel. "
                "Please specify a date (e.g. 'Cancel the game on 2026-04-11').",
            )
            return {"statusCode": 200, "body": "Missing date"}

        existing = get_game_status(game_date)

        if existing is None:
            pre_cancel_game(game_date)
            send_email(
                sender_email,
                f"Re: {subject}",
                f"Done. The game on {game_date} has been pre-cancelled. "
                f"Players will be notified on Monday that there is no game this week.",
            )
            logger.info(f"Pre-cancelled game for {game_date}")

        elif existing.get("status") == "OPEN":
            update_game_status(game_date, "CANCELLED")
            roster = get_roster(game_date)

            notified: set[str] = set()
            for status_key in ("YES", "MAYBE"):
                for player_email in roster.get(status_key, {}).get("players", {}).keys():
                    send_admin_cancelled_broadcast(player_email, game_date, include_unsubscribe=True)
                    notified.add(player_email)
                for guest in roster.get(status_key, {}).get("guests", []):
                    if guest.get("sk") == "guest#active":
                        send_admin_cancelled_broadcast(guest["pk"], game_date)
                        notified.add(guest["pk"])

            send_email(
                sender_email,
                f"Re: {subject}",
                f"Done. The game on {game_date} has been cancelled. "
                f"Notified {len(notified)} player(s) and guest(s) who had responded YES or MAYBE.",
            )
            logger.info(f"Cancelled open game {game_date}, notified {len(notified)} players/guests")

        else:
            send_email(
                sender_email,
                f"Re: {subject}",
                f"The game on {game_date} is already {existing.get('status')}. No changes made.",
            )

    elif intent in ("ADD_PLAYER", "ADD_ADMIN"):
        player_email = parsed.get("email")
        player_name = parsed.get("name")
        player_is_admin = intent == "ADD_ADMIN"

        if not player_email or not player_name:
            send_email(
                sender_email,
                f"Re: {subject}",
                "Please provide both an email address and a name. "
                "Example: 'Add player alice@example.com, name Alice'",
            )
            return {"statusCode": 200, "body": "Missing email or name"}

        add_player(player_email, player_name, is_admin=player_is_admin)
        role = "admin" if player_is_admin else "player"
        send_email(
            sender_email,
            f"Re: {subject}",
            f"Done. Added {player_name} ({player_email}) as a {role}.",
        )
        logger.info(f"Added {role} {player_email}")

    elif intent == "DEACTIVATE_PLAYER":
        player_email = parsed.get("email")
        if not player_email:
            send_email(
                sender_email,
                f"Re: {subject}",
                "Please provide the email address of the player to deactivate.",
            )
            return {"statusCode": 200, "body": "Missing email"}

        try:
            deactivate_player(player_email)
            send_email(
                sender_email,
                f"Re: {subject}",
                f"Done. {player_email} has been deactivated and will no longer receive game emails.",
            )
        except ValueError as e:
            send_email(sender_email, f"Re: {subject}", f"Error: {e}")

    elif intent == "REACTIVATE_PLAYER":
        player_email = parsed.get("email")
        if not player_email:
            send_email(
                sender_email,
                f"Re: {subject}",
                "Please provide the email address of the player to reactivate.",
            )
            return {"statusCode": 200, "body": "Missing email"}

        try:
            reactivate_player(player_email)
            send_email(
                sender_email,
                f"Re: {subject}",
                f"Done. {player_email} has been reactivated and will receive future game emails.",
            )
        except ValueError as e:
            send_email(sender_email, f"Re: {subject}", f"Error: {e}")

    else:
        send_email(
            sender_email,
            f"Re: {subject}",
            "I couldn't understand that command. Available commands:\n"
            "- Cancel the game on [date]\n"
            "- Add player [email], name [name]\n"
            "- Add admin [email], name [name]\n"
            "- Deactivate [email]\n"
            "- Reactivate [email]",
        )

    return {"statusCode": 200, "body": {"intent": intent}}
