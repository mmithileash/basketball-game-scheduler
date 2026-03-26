"""Integration tests for the email_processor Lambda flow.

All AWS calls (DynamoDB, S3, SES) go to LocalStack.
Only Bedrock (bedrock_client.parse_player_email) is mocked.
"""

from email.mime.text import MIMEText
from unittest.mock import patch

import boto3
import pytest

from tests.integration.conftest import EMAIL_BUCKET, GAMES_TABLE, resolve_lambda_handler


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ses_event(message_id: str) -> dict:
    """Build a minimal SES inbound event that the handler expects."""
    return {
        "Records": [
            {
                "ses": {
                    "mail": {
                        "messageId": message_id,
                    }
                }
            }
        ]
    }


def _put_email_in_s3(sender: str, subject: str, body_text: str, key: str):
    """Create a raw MIME email and upload it to the S3 bucket on LocalStack."""
    msg = MIMEText(body_text)
    msg["From"] = f"{sender}"
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = subject

    s3 = boto3.client("s3", region_name="eu-west-1")
    s3.put_object(Bucket=EMAIL_BUCKET, Key=key, Body=msg.as_bytes())


def _bedrock_response(intent, guest_names=None, reply="Got it!"):
    """Return a dict shaped like bedrock_client.parse_player_email output."""
    return {
        "intent": intent,
        "guest_count": len(guest_names) if guest_names else 0,
        "guest_names": guest_names or [],
        "query_target": None,
        "reply_draft": reply,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlayerJoins:
    def test_player_joins(
        self, dynamodb_tables, s3_bucket, ses_identity, seed_players, seed_game
    ):
        """A new player replies YES -- added to playerStatus#YES in DynamoDB."""
        message_id = "msg-join-001"
        _put_email_in_s3(
            sender="eve@example.com",
            subject="Re: Basketball Game - 2026-03-28",
            body_text="I'm in!",
            key=message_id,
        )

        with patch(
            "email_processor.handler.parse_player_email",
            return_value=_bedrock_response("JOIN", reply="You're in!"),
        ):
            handler_fn = resolve_lambda_handler("email_processor")
            result = handler_fn(_make_ses_event(message_id), None)

        assert result["statusCode"] == 200
        assert result["body"]["intent"] == "JOIN"

        # Verify DynamoDB
        table = dynamodb_tables.Table(GAMES_TABLE)
        yes_item = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#YES"}
        )["Item"]
        assert "eve@example.com" in yes_item["players"]


class TestPlayerDeclines:
    def test_player_declines(
        self, dynamodb_tables, s3_bucket, ses_identity, seed_players, seed_game
    ):
        """A new player replies NO -- added to playerStatus#NO."""
        message_id = "msg-decline-001"
        _put_email_in_s3(
            sender="eve@example.com",
            subject="Re: Basketball Game - 2026-03-28",
            body_text="Can't make it",
            key=message_id,
        )

        with patch(
            "email_processor.handler.parse_player_email",
            return_value=_bedrock_response("DECLINE", reply="Sorry to hear that!"),
        ):
            handler_fn = resolve_lambda_handler("email_processor")
            result = handler_fn(_make_ses_event(message_id), None)

        assert result["statusCode"] == 200
        assert result["body"]["intent"] == "DECLINE"

        table = dynamodb_tables.Table(GAMES_TABLE)
        no_item = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#NO"}
        )["Item"]
        assert "eve@example.com" in no_item["players"]


class TestPlayerChangesResponse:
    def test_player_changes_response(
        self, dynamodb_tables, s3_bucket, ses_identity, seed_players, seed_game
    ):
        """Player first joins (YES) then declines (NO).
        TransactWriteItems atomically moves them between status maps."""

        # --- Step 1: JOIN ---
        msg_id_1 = "msg-change-001"
        _put_email_in_s3(
            sender="eve@example.com",
            subject="Re: Basketball Game - 2026-03-28",
            body_text="Count me in",
            key=msg_id_1,
        )

        with patch(
            "email_processor.handler.parse_player_email",
            return_value=_bedrock_response("JOIN"),
        ):
            handler_fn = resolve_lambda_handler("email_processor")
            handler_fn(_make_ses_event(msg_id_1), None)

        table = dynamodb_tables.Table(GAMES_TABLE)
        yes_item = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#YES"}
        )["Item"]
        assert "eve@example.com" in yes_item["players"]

        # --- Step 2: DECLINE (change from YES to NO) ---
        msg_id_2 = "msg-change-002"
        _put_email_in_s3(
            sender="eve@example.com",
            subject="Re: Basketball Game - 2026-03-28",
            body_text="Sorry, can't make it after all",
            key=msg_id_2,
        )

        with patch(
            "email_processor.handler.parse_player_email",
            return_value=_bedrock_response("DECLINE"),
        ):
            result = handler_fn(_make_ses_event(msg_id_2), None)

        assert result["body"]["intent"] == "DECLINE"

        # Verify transactional move: removed from YES, present in NO
        yes_item = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#YES"}
        )["Item"]
        assert "eve@example.com" not in yes_item["players"]

        no_item = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#NO"}
        )["Item"]
        assert "eve@example.com" in no_item["players"]


class TestPlayerBringsGuests:
    def test_player_brings_guests(
        self, dynamodb_tables, s3_bucket, ses_identity, seed_players, seed_game
    ):
        """Player joins with guests -- guests stored in the players map."""
        message_id = "msg-guests-001"
        _put_email_in_s3(
            sender="eve@example.com",
            subject="Re: Basketball Game - 2026-03-28",
            body_text="I'm in, bringing 2 friends: John, Jane",
            key=message_id,
        )

        with patch(
            "email_processor.handler.parse_player_email",
            return_value=_bedrock_response(
                "BRING_GUESTS",
                guest_names=["John", "Jane"],
                reply="You and your guests are in!",
            ),
        ):
            handler_fn = resolve_lambda_handler("email_processor")
            result = handler_fn(_make_ses_event(message_id), None)

        assert result["statusCode"] == 200
        assert result["body"]["intent"] == "BRING_GUESTS"

        table = dynamodb_tables.Table(GAMES_TABLE)
        yes_item = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#YES"}
        )["Item"]

        assert "eve@example.com" in yes_item["players"]
        guest_data = yes_item["players"]["eve@example.com"]
        assert "John" in guest_data["guests"]
        assert "Jane" in guest_data["guests"]


class TestQueryRosterNoDbChange:
    def test_query_roster_no_db_change(
        self, dynamodb_tables, s3_bucket, ses_identity, seed_players, seed_game
    ):
        """QUERY_ROSTER intent should NOT modify DynamoDB at all."""

        # Snapshot the current roster state
        table = dynamodb_tables.Table(GAMES_TABLE)
        before_yes = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#YES"}
        )["Item"]["players"]
        before_no = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#NO"}
        )["Item"]["players"]
        before_maybe = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#MAYBE"}
        )["Item"]["players"]

        message_id = "msg-query-001"
        _put_email_in_s3(
            sender="eve@example.com",
            subject="Re: Basketball Game - 2026-03-28",
            body_text="Who's playing this week?",
            key=message_id,
        )

        with patch(
            "email_processor.handler.parse_player_email",
            return_value=_bedrock_response(
                "QUERY_ROSTER", reply="Here's the current roster..."
            ),
        ):
            handler_fn = resolve_lambda_handler("email_processor")
            result = handler_fn(_make_ses_event(message_id), None)

        assert result["body"]["intent"] == "QUERY_ROSTER"

        # Verify nothing changed
        after_yes = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#YES"}
        )["Item"]["players"]
        after_no = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#NO"}
        )["Item"]["players"]
        after_maybe = table.get_item(
            Key={"gameDate": "2026-03-28", "sk": "playerStatus#MAYBE"}
        )["Item"]["players"]

        assert before_yes == after_yes
        assert before_no == after_no
        assert before_maybe == after_maybe
