"""Integration tests for the game_finalizer Lambda flow.

All DynamoDB calls go to LocalStack. The date is mocked to simulate
the Lambda running on a Saturday after the game finishes.
"""

import datetime
from unittest.mock import patch

import boto3
import pytest

from tests.integration.conftest import GAMES_TABLE, resolve_lambda_handler


pytestmark = pytest.mark.integration

GAME_DATE = "2026-03-28"  # A Saturday
FAKE_SATURDAY = datetime.date(2026, 3, 28)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_game_with_status(status: str) -> None:
    client = boto3.client("dynamodb", region_name="eu-west-1")
    items = [
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": GAME_DATE},
                    "sk": {"S": "gameStatus"},
                    "status": {"S": status},
                    "createdAt": {"S": "2026-03-23T09:00:00+00:00"},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": GAME_DATE},
                    "sk": {"S": "playerStatus#YES"},
                    "players": {
                        "M": {
                            "alice@example.com": {"M": {"guests": {"L": []}}},
                            "bob@example.com": {"M": {"guests": {"L": []}}},
                        }
                    },
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": GAME_DATE},
                    "sk": {"S": "playerStatus#NO"},
                    "players": {"M": {}},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": GAME_DATE},
                    "sk": {"S": "playerStatus#MAYBE"},
                    "players": {"M": {}},
                }
            }
        },
    ]
    client.batch_write_item(RequestItems={GAMES_TABLE: items})


def _cleanup_game(dynamodb_tables) -> None:
    table = dynamodb_tables.Table(GAMES_TABLE)
    for sk in ("gameStatus", "playerStatus#YES", "playerStatus#NO", "playerStatus#MAYBE"):
        table.delete_item(Key={"gameDate": GAME_DATE, "sk": sk})


def _get_game_status(dynamodb_tables) -> str | None:
    table = dynamodb_tables.Table(GAMES_TABLE)
    item = table.get_item(Key={"gameDate": GAME_DATE, "sk": "gameStatus"}).get("Item")
    return item["status"] if item else None


def _run_handler():
    with patch("game_finalizer.handler.date") as mock_date:
        mock_date.today.return_value = FAKE_SATURDAY
        mock_date.side_effect = lambda *a, **k: datetime.date(*a, **k)
        handler_fn = resolve_lambda_handler("game_finalizer")
        return handler_fn({}, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenGameMarkedPlayed:
    def test_open_game_marked_played(self, dynamodb_tables):
        """OPEN game on Saturday should be marked PLAYED in DynamoDB."""
        _seed_game_with_status("OPEN")

        try:
            result = _run_handler()

            assert result["statusCode"] == 200
            assert result["body"]["action"] == "game_marked_played"
            assert result["body"]["gameDate"] == GAME_DATE

            assert _get_game_status(dynamodb_tables) == "PLAYED"
        finally:
            _cleanup_game(dynamodb_tables)


class TestCancelledGameNotChanged:
    def test_cancelled_game_not_changed(self, dynamodb_tables):
        """CANCELLED game should remain CANCELLED — never transition to PLAYED."""
        _seed_game_with_status("CANCELLED")

        try:
            result = _run_handler()

            assert result["statusCode"] == 200
            assert result["body"]["action"] == "no_action"
            assert result["body"]["status"] == "CANCELLED"

            assert _get_game_status(dynamodb_tables) == "CANCELLED"
        finally:
            _cleanup_game(dynamodb_tables)


class TestAlreadyPlayedIsNoOp:
    def test_already_played_is_no_op(self, dynamodb_tables):
        """Already PLAYED game should not be updated again (idempotent)."""
        _seed_game_with_status("PLAYED")

        try:
            result = _run_handler()

            assert result["statusCode"] == 200
            assert result["body"]["action"] == "no_action"
            assert result["body"]["status"] == "PLAYED"

            assert _get_game_status(dynamodb_tables) == "PLAYED"
        finally:
            _cleanup_game(dynamodb_tables)


class TestNoGameReturnsEarly:
    def test_no_game_returns_early(self, dynamodb_tables):
        """No game record for today should return early with no DynamoDB write."""
        # Ensure no game exists for the date
        _cleanup_game(dynamodb_tables)

        result = _run_handler()

        assert result["statusCode"] == 200
        assert result["body"] == "No game found"
        assert _get_game_status(dynamodb_tables) is None
