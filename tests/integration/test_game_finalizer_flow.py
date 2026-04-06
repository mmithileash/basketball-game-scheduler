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
                            "alice@example.com": {"M": {"name": {"S": "Alice"}}},
                            "bob@example.com": {"M": {"name": {"S": "Bob"}}},
                        }
                    },
                    "guests": {"L": []},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": GAME_DATE},
                    "sk": {"S": "playerStatus#NO"},
                    "players": {"M": {}},
                    "guests": {"L": []},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": GAME_DATE},
                    "sk": {"S": "playerStatus#MAYBE"},
                    "players": {"M": {}},
                    "guests": {"L": []},
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


@pytest.mark.integration
def test_guest_cleanup_after_game_finalizer(
    dynamodb_tables, seed_players, ses_identity
):
    """Full flow: create game, add guests, run game_finalizer, verify guest entries deleted."""
    from datetime import date
    from unittest.mock import patch

    game_date = date.today().isoformat()

    from common.dynamo import create_game, add_guests_to_game_status, create_guest_entry, get_roster
    create_game(game_date)

    # Add a guest entry (simulating BRING_GUESTS flow)
    guest_obj = create_guest_entry(
        game_date=game_date,
        guest_name="TestGuest",
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        contact_email="testguest@example.com",
    )
    add_guests_to_game_status(game_date, "YES", [guest_obj])

    # Verify guest exists in Players table
    players_table = dynamodb_tables.Table("Players")
    item = players_table.get_item(
        Key={"email": "testguest@example.com", "active": "guest#active"}
    ).get("Item")
    assert item is not None
    assert item["name"] == "TestGuest"

    games_table = dynamodb_tables.Table("Games")
    try:
        # Run game_finalizer
        with patch("game_finalizer.handler.date") as mock_date:
            mock_date.today.return_value = date.fromisoformat(game_date)
            from game_finalizer.handler import handler as finalizer_handler
            result = finalizer_handler({}, None)

        assert result["statusCode"] == 200
        assert result["body"]["action"] == "game_marked_played"
        assert result["body"]["guestsDeleted"] == 1

        # Verify guest entry deleted from Players table
        item_after = players_table.get_item(
            Key={"email": "testguest@example.com", "active": "guest#active"}
        ).get("Item")
        assert item_after is None
    finally:
        for sk in ("gameStatus", "playerStatus#YES", "playerStatus#NO", "playerStatus#MAYBE"):
            games_table.delete_item(Key={"gameDate": game_date, "sk": sk})
        # Best-effort: delete guest entry in case handler didn't (e.g. assertion failure)
        players_table.delete_item(Key={"email": "testguest@example.com", "active": "guest#active"})


@pytest.mark.integration
def test_decline_with_guests_moves_to_no(dynamodb_tables, seed_players, ses_identity):
    """Player declines: their guests move from YES to NO guests array."""
    from datetime import date

    game_date = "2026-05-10"  # Fixed date to avoid collision with other tests

    from common.dynamo import (
        create_game, add_guests_to_game_status, create_guest_entry,
        update_player_response, get_roster,
        remove_sponsor_guests_from_status,
    )
    create_game(game_date)
    update_player_response(game_date, "alice@example.com", "YES", name="Alice")

    guest_obj = create_guest_entry(
        game_date=game_date,
        guest_name="John",
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        contact_email="john@example.com",
    )
    add_guests_to_game_status(game_date, "YES", [guest_obj])

    # Verify guest is in YES
    roster = get_roster(game_date)
    assert len(roster["YES"]["guests"]) == 1

    # Simulate DECLINE by alice
    update_player_response(game_date, "alice@example.com", "NO", name="Alice", old_status="YES")
    guests = remove_sponsor_guests_from_status(game_date, "YES", "alice@example.com")
    add_guests_to_game_status(game_date, "NO", guests)

    # Verify guest moved to NO
    roster = get_roster(game_date)
    assert len(roster["YES"]["guests"]) == 0
    assert len(roster["NO"]["guests"]) == 1
    assert roster["NO"]["guests"][0]["name"] == "John"

    # Cleanup
    games_table = dynamodb_tables.Table("Games")
    players_table = dynamodb_tables.Table("Players")
    for sk in ("gameStatus", "playerStatus#YES", "playerStatus#NO", "playerStatus#MAYBE"):
        games_table.delete_item(Key={"gameDate": game_date, "sk": sk})
    players_table.delete_item(Key={"email": "john@example.com", "active": "guest#active"})
