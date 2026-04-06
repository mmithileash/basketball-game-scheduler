from datetime import date
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from common.dynamo import (
    _next_saturday,
    create_game,
    get_active_players,
    get_current_open_game,
    get_game_status,
    get_pending_players,
    get_roster,
    update_game_status,
    update_player_response,
)


def _create_tables():
    """Helper to create DynamoDB tables inside a moto mock context."""
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")

    dynamodb.create_table(
        TableName="test-players",
        KeySchema=[{"AttributeName": "email", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "email", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    dynamodb.create_table(
        TableName="test-games",
        KeySchema=[
            {"AttributeName": "gameDate", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "gameDate", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return dynamodb


def _reset_dynamo_caches():
    import common.dynamo as dynamo_mod
    dynamo_mod._config = None
    dynamo_mod._dynamodb = None
    dynamo_mod._client = None


@pytest.mark.unit
@mock_aws
def test_get_active_players(sample_players):
    """Seed players with mix of active/inactive, verify only active returned."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()
    table = dynamodb.Table("test-players")

    for player in sample_players:
        item = {"email": player["email"], "active": player["active"]}
        if player["name"]:
            item["name"] = player["name"]
        table.put_item(Item=item)

    result = get_active_players()

    assert len(result) == 4  # alice, bob, charlie, dave (all active)
    emails = {p["email"] for p in result}
    assert "eve@example.com" not in emails
    assert "alice@example.com" in emails
    assert "bob@example.com" in emails


@pytest.mark.unit
@mock_aws
def test_create_game(sample_game_date):
    """Create game, verify all 4 items exist."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()

    create_game(sample_game_date)

    table = dynamodb.Table("test-games")
    response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("gameDate").eq(sample_game_date)
    )
    items = response["Items"]

    assert len(items) == 4

    sks = {item["sk"] for item in items}
    assert "gameStatus" in sks
    assert "playerStatus#YES" in sks
    assert "playerStatus#NO" in sks
    assert "playerStatus#MAYBE" in sks

    # Verify gameStatus item
    game_status_item = next(i for i in items if i["sk"] == "gameStatus")
    assert game_status_item["status"] == "OPEN"
    assert "createdAt" in game_status_item

    # Verify playerStatus items have empty players maps
    for sk in ("playerStatus#YES", "playerStatus#NO", "playerStatus#MAYBE"):
        item = next(i for i in items if i["sk"] == sk)
        assert item["players"] == {}


@pytest.mark.unit
@mock_aws
def test_get_game_status(sample_game_date):
    """Create game, verify status and createdAt returned."""
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)

    result = get_game_status(sample_game_date)

    assert result is not None
    assert result["status"] == "OPEN"
    assert "createdAt" in result
    assert result["gameDate"] == sample_game_date


@pytest.mark.unit
@mock_aws
def test_get_game_status_not_found():
    """Verify None returned for non-existent game."""
    _reset_dynamo_caches()
    _create_tables()

    result = get_game_status("2099-01-01")
    assert result is None


@pytest.mark.unit
@mock_aws
def test_get_roster(sample_game_date):
    """Create game, manually add players, verify roster structure."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()

    create_game(sample_game_date)

    # Manually add players to the roster via update_player_response
    update_player_response(sample_game_date, "alice@example.com", "YES")
    update_player_response(sample_game_date, "bob@example.com", "NO")
    update_player_response(sample_game_date, "charlie@example.com", "MAYBE")

    roster = get_roster(sample_game_date)

    assert "alice@example.com" in roster["YES"]
    assert "bob@example.com" in roster["NO"]
    assert "charlie@example.com" in roster["MAYBE"]
    assert roster["YES"]["alice@example.com"]["guests"] == []


@pytest.mark.unit
@mock_aws
def test_update_player_response_new(sample_game_date):
    """Add player to YES (no old_status), verify."""
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)
    update_player_response(sample_game_date, "alice@example.com", "YES")

    roster = get_roster(sample_game_date)
    assert "alice@example.com" in roster["YES"]
    assert "alice@example.com" not in roster["NO"]
    assert "alice@example.com" not in roster["MAYBE"]


@pytest.mark.unit
@mock_aws
def test_update_player_response_change(sample_game_date):
    """Add player to YES, then change to NO, verify TransactWriteItems."""
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)
    update_player_response(sample_game_date, "alice@example.com", "YES")

    roster = get_roster(sample_game_date)
    assert "alice@example.com" in roster["YES"]

    # Change from YES to NO
    update_player_response(
        sample_game_date, "alice@example.com", "NO", old_status="YES"
    )

    roster = get_roster(sample_game_date)
    assert "alice@example.com" not in roster["YES"]
    assert "alice@example.com" in roster["NO"]


@pytest.mark.unit
@mock_aws
def test_update_player_response_with_guests(sample_game_date):
    """Add player with guests, verify guest names stored."""
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)
    update_player_response(
        sample_game_date, "alice@example.com", "YES",
        guests=["Mike", "Sarah"],
    )

    roster = get_roster(sample_game_date)
    assert "alice@example.com" in roster["YES"]
    assert roster["YES"]["alice@example.com"]["guests"] == ["Mike", "Sarah"]


@pytest.mark.unit
def test_next_saturday_from_monday():
    assert _next_saturday(date(2026, 3, 23)) == date(2026, 3, 28)


@pytest.mark.unit
def test_next_saturday_from_wednesday():
    assert _next_saturday(date(2026, 3, 25)) == date(2026, 3, 28)


@pytest.mark.unit
def test_next_saturday_from_friday():
    assert _next_saturday(date(2026, 3, 27)) == date(2026, 3, 28)


@pytest.mark.unit
def test_next_saturday_from_saturday():
    assert _next_saturday(date(2026, 3, 28)) == date(2026, 3, 28)


@pytest.mark.unit
def test_next_saturday_from_sunday():
    assert _next_saturday(date(2026, 3, 29)) == date(2026, 4, 4)


@pytest.mark.unit
@mock_aws
@patch("common.dynamo.date", wraps=date)
def test_get_current_open_game_found(mock_date, sample_game_date):
    """Create OPEN game for upcoming Saturday, verify found."""
    mock_date.today.return_value = date(2026, 3, 25)  # Wednesday
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)  # "2026-03-28" (Saturday)

    result = get_current_open_game()
    assert result is not None
    assert result["gameDate"] == sample_game_date
    assert result["status"] == "OPEN"


@pytest.mark.unit
@mock_aws
@patch("common.dynamo.date", wraps=date)
def test_get_current_open_game_cancelled(mock_date, sample_game_date):
    """CANCELLED game for upcoming Saturday should not be returned."""
    mock_date.today.return_value = date(2026, 3, 25)  # Wednesday
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)
    update_game_status(sample_game_date, "CANCELLED")

    result = get_current_open_game()
    assert result is None


@pytest.mark.unit
@mock_aws
@patch("common.dynamo.date", wraps=date)
def test_get_current_open_game_no_game(mock_date):
    """No game seeded, verify None returned."""
    mock_date.today.return_value = date(2026, 3, 25)  # Wednesday
    _reset_dynamo_caches()
    _create_tables()

    result = get_current_open_game()
    assert result is None


@pytest.mark.unit
@mock_aws
@patch("common.dynamo.date", wraps=date)
def test_get_current_open_game_played(mock_date, sample_game_date):
    """PLAYED game for upcoming Saturday should not be returned."""
    mock_date.today.return_value = date(2026, 3, 25)  # Wednesday
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)
    update_game_status(sample_game_date, "PLAYED")

    result = get_current_open_game()
    assert result is None


@pytest.mark.unit
@mock_aws
def test_get_pending_players(sample_players, sample_game_date):
    """Seed players and partial roster, verify pending list is correct."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()
    players_table = dynamodb.Table("test-players")

    for player in sample_players:
        item = {"email": player["email"], "active": player["active"]}
        if player["name"]:
            item["name"] = player["name"]
        players_table.put_item(Item=item)

    create_game(sample_game_date)

    # Only alice and bob have responded
    update_player_response(sample_game_date, "alice@example.com", "YES")
    update_player_response(sample_game_date, "bob@example.com", "NO")

    pending = get_pending_players(sample_game_date)
    pending_emails = {p["email"] for p in pending}

    # charlie and dave are active but haven't responded
    assert "charlie@example.com" in pending_emails
    assert "dave@example.com" in pending_emails
    # alice and bob responded
    assert "alice@example.com" not in pending_emails
    assert "bob@example.com" not in pending_emails
    # eve is inactive
    assert "eve@example.com" not in pending_emails
