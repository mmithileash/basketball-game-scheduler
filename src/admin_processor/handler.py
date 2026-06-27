import json
import logging
import os
from datetime import date, timedelta
from typing import Any

import boto3

from common.bedrock_client import parse_admin_email
from common.config import load_config
from common.date_utils import sfn_timestamps_for_game, week_start_for_date
from common.policy import default_policy, fixed_policy
from common.dynamo import (
    add_player,
    create_game,
    deactivate_player,
    get_active_players,
    get_game_status,
    get_roster,
    is_admin,
    pre_cancel_game,
    reactivate_player,
    set_week_no_game,
    update_game_status,
)
from common.email_service import (
    send_admin_cancelled_broadcast,
    send_email,
    send_no_game_this_week,
)
from common.email_utils import fetch_email_from_s3

_s3_client = None
_sfn_client = None


def _get_sfn_client():
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions")
    return _sfn_client

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

    if intent == "SCHEDULE_GAMES":
        games_to_schedule = parsed.get("games", [])
        if not games_to_schedule:
            send_email(
                sender_email,
                f"Re: {subject}",
                "I couldn't parse any game dates from your message. "
                "Please specify dates like 'Tuesday July 7' or 'Saturday July 12'.",
            )
            return {"statusCode": 200, "body": "No games parsed"}

        config = load_config()

        # Classify each game by how many of (start time, duration) the admin gave.
        partials: list[str] = []
        plans: list[tuple[str, dict[str, Any]]] = []
        for game_info in games_to_schedule:
            game_date = game_info.get("date")
            if not game_date:
                continue
            start_time = game_info.get("startTime")
            duration_hours = game_info.get("durationHours")
            has_time = start_time is not None
            has_duration = duration_hours is not None

            if has_time and has_duration:
                plans.append((game_date, fixed_policy(
                    start_time,
                    int(duration_hours),
                    threshold=config.long_game_threshold,
                    min_players=config.min_players,
                )))
            elif not has_time and not has_duration:
                plans.append((game_date, default_policy(config)))
            else:
                missing = "duration" if has_time else "start time"
                given = f"start time {start_time}" if has_time else f"duration {duration_hours}h"
                partials.append(
                    f"  - {game_date}: you gave a {given} but no {missing}."
                )

        # Any partial spec holds the whole batch: create nothing, ask for a clean resend.
        if partials:
            send_email(
                sender_email,
                f"Re: {subject}",
                "I couldn't schedule your games because some were incomplete. "
                "A game needs either no time at all (and I'll use the default tiers) "
                "or both a start time and a duration.\n\n"
                + "\n".join(partials)
                + "\n\nNothing was scheduled. Please resend the complete command.",
            )
            return {"statusCode": 200, "body": "Partial spec, batch held"}

        sfn_arn = os.environ.get("GAME_LIFECYCLE_SFN_ARN")
        sfn = _get_sfn_client() if sfn_arn else None
        scheduled: list[str] = []

        for game_date, policy in plans:
            create_game(game_date, policy)
            if sfn and sfn_arn:
                execution_input = sfn_timestamps_for_game(game_date)
                try:
                    sfn.start_execution(
                        stateMachineArn=sfn_arn,
                        name=f"game-{game_date}",
                        input=json.dumps(execution_input),
                    )
                    logger.info(f"Started SFN execution game-{game_date}")
                except sfn.exceptions.ExecutionAlreadyExists:
                    logger.info(f"SFN execution game-{game_date} already exists, skipping")
            scheduled.append(game_date)
            logger.info(f"Scheduled game for {game_date}")

        send_email(
            sender_email,
            f"Re: {subject}",
            f"Done. Scheduled {len(scheduled)} game(s): {', '.join(scheduled)}. "
            f"Players will be notified 7 days before each game.",
        )

    elif intent == "NO_GAMES_THIS_WEEK":
        today = date.today()
        week_start_str = week_start_for_date(today + timedelta(days=7)).isoformat()
        set_week_no_game(week_start_str, "admin_declined")

        players = get_active_players()
        for player in players:
            try:
                send_no_game_this_week(
                    player["email"], player.get("name"), week_start_str, "admin_declined"
                )
            except Exception:
                logger.error(f"Failed to notify {player['email']}", exc_info=True)

        send_email(
            sender_email,
            f"Re: {subject}",
            f"Done. Players have been notified there are no games this week ({week_start_str}).",
        )
        logger.info(f"Admin declined games for week {week_start_str}, notified {len(players)} player(s)")

    elif intent == "CANCEL_GAME":
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
            _stop_game_sfn_execution(game_date)
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
            "- Schedule games: 'Tuesday and Saturday'\n"
            "- No games this week\n"
            "- Cancel the game on [date]\n"
            "- Add player [email], name [name]\n"
            "- Add admin [email], name [name]\n"
            "- Deactivate [email]\n"
            "- Reactivate [email]",
        )

    return {"statusCode": 200, "body": {"intent": intent}}


def _stop_game_sfn_execution(game_date: str) -> None:
    """Stop the SFN execution for a game, silently ignoring if not found."""
    sfn_arn = os.environ.get("GAME_LIFECYCLE_SFN_ARN")
    if not sfn_arn:
        return
    execution_arn = sfn_arn.replace(":stateMachine:", ":execution:") + f":game-{game_date}"
    try:
        _get_sfn_client().stop_execution(
            executionArn=execution_arn,
            cause="Admin cancelled game",
        )
        logger.info(f"Stopped SFN execution game-{game_date}")
    except Exception as e:
        if "ExecutionDoesNotExist" in type(e).__name__ or "does not exist" in str(e).lower():
            logger.info(f"SFN execution game-{game_date} not found (already finished or not started)")
        else:
            logger.warning(f"Failed to stop SFN execution game-{game_date}: {e}")
