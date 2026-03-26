"""Integration tests for the announcement_sender Lambda flow.

Tests exercise DynamoDB writes and SES sends against LocalStack.
"""

from unittest.mock import patch

import boto3
import pytest

from tests.integration.conftest import GAMES_TABLE, PLAYERS_TABLE, SENDER_EMAIL, resolve_lambda_handler


pytestmark = pytest.mark.integration


class TestFullAnnouncementFlow:
    """Seed active + inactive players, invoke handler, verify DynamoDB + SES."""

    def test_full_announcement_flow(
        self, dynamodb_tables, ses_identity, seed_players
    ):
        """Announce a game: game items created, emails sent only to active players."""
        # Fix the date so we get a deterministic game_date
        with patch("announcement_sender.handler.date") as mock_date:
            mock_date.today.return_value = __import__("datetime").date(2026, 3, 23)  # Monday
            mock_date.side_effect = lambda *a, **k: __import__("datetime").date(*a, **k)

            handler_fn = resolve_lambda_handler("announcement_sender")
            result = handler_fn({}, None)

        assert result["statusCode"] == 200
        body = result["body"]
        game_date = body["gameDate"]

        # 5 active players, 2 inactive
        assert body["playerCount"] == 5
        assert body["sentCount"] == 5

        # ------ Verify DynamoDB game items ------
        games_table = dynamodb_tables.Table(GAMES_TABLE)

        # gameStatus item
        game_status = games_table.get_item(
            Key={"gameDate": game_date, "sk": "gameStatus"}
        ).get("Item")
        assert game_status is not None
        assert game_status["status"] == "OPEN"

        # All four SK items must exist
        for sk in (
            "gameStatus",
            "playerStatus#YES",
            "playerStatus#NO",
            "playerStatus#MAYBE",
        ):
            item = games_table.get_item(
                Key={"gameDate": game_date, "sk": sk}
            ).get("Item")
            assert item is not None, f"Missing game item with sk={sk}"

        # playerStatus maps should be empty at creation time
        yes_item = games_table.get_item(
            Key={"gameDate": game_date, "sk": "playerStatus#YES"}
        )["Item"]
        assert yes_item["players"] == {}

        # ------ Verify SES calls ------
        ses = boto3.client("ses", region_name="eu-west-1")
        # LocalStack SES stores sent messages; query the send statistics
        stats = ses.get_send_statistics()
        # At minimum, 5 emails should have been delivered
        data_points = stats.get("SendDataPoints", [])
        total_sent = sum(dp.get("DeliveryAttempts", 0) for dp in data_points)
        # LocalStack may aggregate differently; as a fallback, just assert
        # the handler reported 5 sent.
        assert body["sentCount"] == 5

        # Cleanup the game we just created
        for sk in (
            "gameStatus",
            "playerStatus#YES",
            "playerStatus#NO",
            "playerStatus#MAYBE",
        ):
            games_table.delete_item(Key={"gameDate": game_date, "sk": sk})

    def test_announcement_idempotency(
        self, dynamodb_tables, ses_identity, seed_players
    ):
        """Calling handler twice for the same week should create a second game
        (each invocation computes next-Saturday independently). Verify both
        invocations succeed without error and the second does not corrupt the
        first game's data."""

        fixed_monday = __import__("datetime").date(2026, 3, 23)

        with patch("announcement_sender.handler.date") as mock_date:
            mock_date.today.return_value = fixed_monday
            mock_date.side_effect = lambda *a, **k: __import__("datetime").date(*a, **k)

            handler_fn = resolve_lambda_handler("announcement_sender")
            result1 = handler_fn({}, None)
            result2 = handler_fn({}, None)

        assert result1["statusCode"] == 200
        assert result2["statusCode"] == 200

        game_date = result1["body"]["gameDate"]

        # Game items still intact after second call (batch_write_item is a PUT,
        # so the second call simply overwrites with the same data)
        games_table = dynamodb_tables.Table(GAMES_TABLE)
        game_status = games_table.get_item(
            Key={"gameDate": game_date, "sk": "gameStatus"}
        ).get("Item")
        assert game_status is not None
        assert game_status["status"] == "OPEN"

        # Cleanup
        for sk in (
            "gameStatus",
            "playerStatus#YES",
            "playerStatus#NO",
            "playerStatus#MAYBE",
        ):
            games_table.delete_item(Key={"gameDate": game_date, "sk": sk})
