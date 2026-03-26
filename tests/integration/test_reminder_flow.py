"""Integration tests for the reminder_checker Lambda flow.

All AWS calls (DynamoDB, SES) go to LocalStack.
Only dates are mocked to simulate Wednesday / Friday behaviour.
"""

import datetime
from unittest.mock import patch

import boto3
import pytest

from tests.integration.conftest import GAMES_TABLE, PLAYERS_TABLE, resolve_lambda_handler


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_game_with_n_confirmed(dynamodb_tables, game_date, confirmed_emails):
    """Insert a game where only the given emails are in playerStatus#YES."""
    client = boto3.client("dynamodb", region_name="eu-west-1")

    yes_map = {}
    for email in confirmed_emails:
        yes_map[email] = {"M": {"guests": {"L": []}}}

    items = [
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "gameStatus"},
                    "status": {"S": "OPEN"},
                    "createdAt": {"S": "2026-03-23T00:00:00+00:00"},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "playerStatus#YES"},
                    "players": {"M": yes_map},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "playerStatus#NO"},
                    "players": {"M": {}},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "playerStatus#MAYBE"},
                    "players": {"M": {}},
                }
            }
        },
    ]

    client.batch_write_item(RequestItems={GAMES_TABLE: items})


def _cleanup_game(dynamodb_tables, game_date):
    table = dynamodb_tables.Table(GAMES_TABLE)
    for sk in ("gameStatus", "playerStatus#YES", "playerStatus#NO", "playerStatus#MAYBE"):
        table.delete_item(Key={"gameDate": game_date, "sk": sk})


def _seed_five_active_players(dynamodb_tables):
    """Insert 5 active players; return the list of dicts."""
    table = dynamodb_tables.Table(PLAYERS_TABLE)
    players = [
        {"email": "p1@example.com", "name": "P1", "active": "true"},
        {"email": "p2@example.com", "name": "P2", "active": "true"},
        {"email": "p3@example.com", "name": "P3", "active": "true"},
        {"email": "p4@example.com", "name": "P4", "active": "true"},
        {"email": "p5@example.com", "name": "P5", "active": "true"},
    ]
    for p in players:
        table.put_item(Item=p)
    return players


def _cleanup_players(dynamodb_tables, players):
    table = dynamodb_tables.Table(PLAYERS_TABLE)
    for p in players:
        table.delete_item(Key={"email": p["email"], "active": p["active"]})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReminderSentWhenBelowMinimum:
    def test_reminder_sent_when_below_minimum(
        self, dynamodb_tables, ses_identity
    ):
        """Wednesday, <6 confirmed => reminders sent to pending players."""
        game_date = "2026-03-28"
        confirmed = ["p1@example.com", "p2@example.com", "p3@example.com"]
        players = _seed_five_active_players(dynamodb_tables)
        _seed_game_with_n_confirmed(dynamodb_tables, game_date, confirmed)

        # Wednesday = weekday 2
        fake_wednesday = datetime.date(2026, 3, 25)

        try:
            with patch("reminder_checker.handler.date") as mock_date:
                mock_date.today.return_value = fake_wednesday
                mock_date.side_effect = lambda *a, **k: datetime.date(*a, **k)

                handler_fn = resolve_lambda_handler("reminder_checker")
                result = handler_fn({}, None)

            assert result["statusCode"] == 200
            body = result["body"]
            assert body["action"] == "reminders_sent"
            assert body["confirmedCount"] == 3
            # 5 active - 3 confirmed = 2 pending
            assert body["remindersSent"] == 2
        finally:
            _cleanup_game(dynamodb_tables, game_date)
            _cleanup_players(dynamodb_tables, players)


class TestNoReminderWhenAboveMinimum:
    def test_no_reminder_when_above_minimum(
        self, dynamodb_tables, ses_identity
    ):
        """Wednesday, >=6 confirmed => no reminders sent, action is no_action."""
        game_date = "2026-03-28"
        confirmed = [f"p{i}@example.com" for i in range(1, 5)]  # 4 emails, but need >=6
        # Actually seed 8 confirmed by adding extra emails
        confirmed_8 = [
            "p1@example.com", "p2@example.com", "p3@example.com",
            "p4@example.com", "p5@example.com",
            "extra1@example.com", "extra2@example.com", "extra3@example.com",
        ]
        players = _seed_five_active_players(dynamodb_tables)
        _seed_game_with_n_confirmed(dynamodb_tables, game_date, confirmed_8)

        fake_wednesday = datetime.date(2026, 3, 25)

        try:
            with patch("reminder_checker.handler.date") as mock_date:
                mock_date.today.return_value = fake_wednesday
                mock_date.side_effect = lambda *a, **k: datetime.date(*a, **k)

                handler_fn = resolve_lambda_handler("reminder_checker")
                result = handler_fn({}, None)

            assert result["statusCode"] == 200
            body = result["body"]
            # 8 confirmed >= 6, so Wednesday branch is not entered; falls to no_action
            assert body["action"] == "no_action"
            assert body["confirmedCount"] == 8
        finally:
            _cleanup_game(dynamodb_tables, game_date)
            _cleanup_players(dynamodb_tables, players)


class TestCancellationOnFriday:
    def test_cancellation_on_friday(
        self, dynamodb_tables, ses_identity
    ):
        """Friday, <6 confirmed => game CANCELLED, cancellation emails sent."""
        game_date = "2026-03-28"
        confirmed = ["p1@example.com", "p2@example.com", "p3@example.com"]
        players = _seed_five_active_players(dynamodb_tables)
        _seed_game_with_n_confirmed(dynamodb_tables, game_date, confirmed)

        fake_friday = datetime.date(2026, 3, 27)

        try:
            with patch("reminder_checker.handler.date") as mock_date:
                mock_date.today.return_value = fake_friday
                mock_date.side_effect = lambda *a, **k: datetime.date(*a, **k)

                handler_fn = resolve_lambda_handler("reminder_checker")
                result = handler_fn({}, None)

            assert result["statusCode"] == 200
            body = result["body"]
            assert body["action"] == "game_cancelled"
            assert body["confirmedCount"] == 3

            # Verify game status updated to CANCELLED in DynamoDB
            table = dynamodb_tables.Table(GAMES_TABLE)
            game_status = table.get_item(
                Key={"gameDate": game_date, "sk": "gameStatus"}
            )["Item"]
            assert game_status["status"] == "CANCELLED"
        finally:
            _cleanup_game(dynamodb_tables, game_date)
            _cleanup_players(dynamodb_tables, players)


class TestConfirmationOnFriday:
    def test_confirmation_on_friday(
        self, dynamodb_tables, ses_identity
    ):
        """Friday, >=6 confirmed => confirmation emails sent to YES players."""
        game_date = "2026-03-28"
        confirmed_8 = [
            "p1@example.com", "p2@example.com", "p3@example.com",
            "p4@example.com", "p5@example.com",
            "extra1@example.com", "extra2@example.com", "extra3@example.com",
        ]
        players = _seed_five_active_players(dynamodb_tables)
        _seed_game_with_n_confirmed(dynamodb_tables, game_date, confirmed_8)

        fake_friday = datetime.date(2026, 3, 27)

        try:
            with patch("reminder_checker.handler.date") as mock_date:
                mock_date.today.return_value = fake_friday
                mock_date.side_effect = lambda *a, **k: datetime.date(*a, **k)

                handler_fn = resolve_lambda_handler("reminder_checker")
                result = handler_fn({}, None)

            assert result["statusCode"] == 200
            body = result["body"]
            assert body["action"] == "game_confirmed"
            assert body["confirmedCount"] == 8

            # Game status should still be OPEN (handler sends confirmations
            # but doesn't change status to CONFIRMED in the current code)
            table = dynamodb_tables.Table(GAMES_TABLE)
            game_status = table.get_item(
                Key={"gameDate": game_date, "sk": "gameStatus"}
            )["Item"]
            assert game_status["status"] == "OPEN"
        finally:
            _cleanup_game(dynamodb_tables, game_date)
            _cleanup_players(dynamodb_tables, players)
