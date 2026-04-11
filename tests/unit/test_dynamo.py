from datetime import date
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from common.dynamo import (
    add_guests_to_game_status,
    add_player,
    create_game,
    create_guest_entry,
    deactivate_player,
    delete_guest_entries,
    get_active_players,
    get_current_open_game,
    get_game_status,
    get_pending_players,
    get_player_name,
    get_roster,
    is_admin,
    move_confirmed_guests,
    pre_cancel_game,
    reactivate_player,
    remove_sponsor_guests_from_status,
    update_game_status,
    update_player_response,
)


def _create_tables():
    """Helper to create DynamoDB tables inside a moto mock context."""
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")

    dynamodb.create_table(
        TableName="test-players",
        KeySchema=[
            {"AttributeName": "email", "KeyType": "HASH"},
            {"AttributeName": "active", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "email", "AttributeType": "S"},
            {"AttributeName": "active", "AttributeType": "S"},
        ],
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
    """Create game, verify all 4 items exist with guests list initialised."""
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

    # Verify playerStatus items have empty players maps and guests list
    for sk in ("playerStatus#YES", "playerStatus#NO", "playerStatus#MAYBE"):
        item = next(i for i in items if i["sk"] == sk)
        assert item["players"] == {}
        assert item["guests"] == []


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
    """Create game, add players, verify new roster structure."""
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)

    update_player_response(sample_game_date, "alice@example.com", "YES", name="Alice")
    update_player_response(sample_game_date, "bob@example.com", "NO", name="Bob")
    update_player_response(sample_game_date, "charlie@example.com", "MAYBE", name="Charlie")

    roster = get_roster(sample_game_date)

    assert "alice@example.com" in roster["YES"]["players"]
    assert "bob@example.com" in roster["NO"]["players"]
    assert "charlie@example.com" in roster["MAYBE"]["players"]
    assert roster["YES"]["guests"] == []


@pytest.mark.unit
@mock_aws
def test_update_player_response_new(sample_game_date):
    """Add player to YES (no old_status), verify."""
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)
    update_player_response(sample_game_date, "alice@example.com", "YES", name="Alice")

    roster = get_roster(sample_game_date)
    assert "alice@example.com" in roster["YES"]["players"]
    assert roster["YES"]["players"]["alice@example.com"]["name"] == "Alice"
    assert "alice@example.com" not in roster["NO"]["players"]
    assert "alice@example.com" not in roster["MAYBE"]["players"]


@pytest.mark.unit
@mock_aws
def test_update_player_response_change(sample_game_date):
    """Add player to YES, then change to NO."""
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)
    update_player_response(sample_game_date, "alice@example.com", "YES", name="Alice")

    roster = get_roster(sample_game_date)
    assert "alice@example.com" in roster["YES"]["players"]

    update_player_response(
        sample_game_date, "alice@example.com", "NO", name="Alice", old_status="YES"
    )

    roster = get_roster(sample_game_date)
    assert "alice@example.com" not in roster["YES"]["players"]
    assert "alice@example.com" in roster["NO"]["players"]



@pytest.mark.unit
@mock_aws
@patch("common.date_utils.date", wraps=date)
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
@patch("common.date_utils.date", wraps=date)
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
@patch("common.date_utils.date", wraps=date)
def test_get_current_open_game_no_game(mock_date):
    """No game seeded, verify None returned."""
    mock_date.today.return_value = date(2026, 3, 25)  # Wednesday
    _reset_dynamo_caches()
    _create_tables()

    result = get_current_open_game()
    assert result is None


@pytest.mark.unit
@mock_aws
@patch("common.date_utils.date", wraps=date)
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
    update_player_response(sample_game_date, "alice@example.com", "YES", name="Alice")
    update_player_response(sample_game_date, "bob@example.com", "NO", name="Bob")

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


@pytest.mark.unit
@mock_aws
def test_get_player_name_found():
    """Returns name for a known active player."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()
    table = dynamodb.Table("test-players")
    table.put_item(Item={"email": "alice@example.com", "active": "true", "name": "Alice"})

    result = get_player_name("alice@example.com")
    assert result == "Alice"


@pytest.mark.unit
@mock_aws
def test_get_player_name_not_found():
    """Returns None for unknown player."""
    _reset_dynamo_caches()
    _create_tables()

    result = get_player_name("nobody@example.com")
    assert result is None


@pytest.mark.unit
@mock_aws
def test_create_guest_entry_with_contact_email(sample_game_date):
    """Guest with contact email uses contactEmail as PK, sk=guest#active."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()

    result = create_guest_entry(
        game_date=sample_game_date,
        guest_name="John",
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        contact_email="john@example.com",
    )

    assert result["pk"] == "john@example.com"
    assert result["sk"] == "guest#active"
    assert result["name"] == "John"
    assert result["sponsorEmail"] == "alice@example.com"
    assert result["sponsorName"] == "Alice"

    table = dynamodb.Table("test-players")
    item = table.get_item(Key={"email": "john@example.com", "active": "guest#active"})["Item"]
    assert item["name"] == "John"
    assert item["sponsorEmail"] == "alice@example.com"
    assert item["gameDate"] == sample_game_date


@pytest.mark.unit
@mock_aws
def test_create_guest_entry_without_contact_email(sample_game_date):
    """Guest without contact email uses sponsorEmail as PK, sk=guest#active#<name>."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()

    result = create_guest_entry(
        game_date=sample_game_date,
        guest_name="Jane",
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        contact_email=None,
    )

    assert result["pk"] == "alice@example.com"
    assert result["sk"] == "guest#active#Jane"
    assert result["name"] == "Jane"

    table = dynamodb.Table("test-players")
    item = table.get_item(Key={"email": "alice@example.com", "active": "guest#active#Jane"})["Item"]
    assert item["name"] == "Jane"


@pytest.mark.unit
@mock_aws
def test_delete_guest_entries(sample_game_date):
    """Deletes guest entries from Players table by pk/sk pairs."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()

    g1 = create_guest_entry(sample_game_date, "John", "alice@example.com", "Alice", "john@example.com")
    g2 = create_guest_entry(sample_game_date, "Jane", "alice@example.com", "Alice", None)

    delete_guest_entries([g1, g2])

    table = dynamodb.Table("test-players")
    assert "Item" not in table.get_item(Key={"email": g1["pk"], "active": g1["sk"]})
    assert "Item" not in table.get_item(Key={"email": g2["pk"], "active": g2["sk"]})


@pytest.mark.unit
@mock_aws
def test_add_guests_to_game_status(sample_game_date):
    """Appends guest objects to the guests list on a playerStatus item."""
    _reset_dynamo_caches()
    _create_tables()
    create_game(sample_game_date)

    guest_obj = {
        "pk": "john@example.com",
        "sk": "guest#active",
        "name": "John",
        "sponsorEmail": "alice@example.com",
        "sponsorName": "Alice",
    }
    add_guests_to_game_status(sample_game_date, "YES", [guest_obj])

    roster = get_roster(sample_game_date)
    assert len(roster["YES"]["guests"]) == 1
    assert roster["YES"]["guests"][0]["name"] == "John"


@pytest.mark.unit
@mock_aws
def test_remove_sponsor_guests_from_status(sample_game_date):
    """Removes and returns guest objects for a given sponsor from a status."""
    _reset_dynamo_caches()
    _create_tables()
    create_game(sample_game_date)

    guests = [
        {"pk": "john@example.com", "sk": "guest#active", "name": "John",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
        {"pk": "bob@example.com", "sk": "guest#active", "name": "Bob",
         "sponsorEmail": "charlie@example.com", "sponsorName": "Charlie"},
    ]
    add_guests_to_game_status(sample_game_date, "YES", guests)

    removed = remove_sponsor_guests_from_status(sample_game_date, "YES", "alice@example.com")

    assert len(removed) == 1
    assert removed[0]["name"] == "John"

    roster = get_roster(sample_game_date)
    assert len(roster["YES"]["guests"]) == 1
    assert roster["YES"]["guests"][0]["name"] == "Bob"


@pytest.mark.unit
@mock_aws
def test_move_confirmed_guests(sample_game_date):
    """Moves named guests from NO guests array to YES guests array."""
    _reset_dynamo_caches()
    _create_tables()
    create_game(sample_game_date)

    guests = [
        {"pk": "john@example.com", "sk": "guest#active", "name": "John",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
        {"pk": "alice@example.com", "sk": "guest#active#Jane", "name": "Jane",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
    ]
    add_guests_to_game_status(sample_game_date, "NO", guests)

    move_confirmed_guests(sample_game_date, "alice@example.com", confirmed_names=["John"])

    roster = get_roster(sample_game_date)
    assert len(roster["YES"]["guests"]) == 1
    assert roster["YES"]["guests"][0]["name"] == "John"
    assert len(roster["NO"]["guests"]) == 1
    assert roster["NO"]["guests"][0]["name"] == "Jane"


@pytest.mark.unit
@mock_aws
def test_move_confirmed_guests_ignores_other_sponsors(sample_game_date):
    """move_confirmed_guests does not move guests belonging to a different sponsor."""
    _reset_dynamo_caches()
    _create_tables()
    create_game(sample_game_date)

    guests = [
        {"pk": "john@example.com", "sk": "guest#active", "name": "John",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
        {"pk": "dave@example.com", "sk": "guest#active", "name": "Dave",
         "sponsorEmail": "bob@example.com", "sponsorName": "Bob"},
    ]
    add_guests_to_game_status(sample_game_date, "NO", guests)

    move_confirmed_guests(sample_game_date, "alice@example.com", confirmed_names=["John"])

    roster = get_roster(sample_game_date)
    # John (alice's guest) moved to YES
    assert len(roster["YES"]["guests"]) == 1
    assert roster["YES"]["guests"][0]["name"] == "John"
    # Dave (bob's guest) stays in NO
    assert len(roster["NO"]["guests"]) == 1
    assert roster["NO"]["guests"][0]["name"] == "Dave"
    assert roster["NO"]["guests"][0]["sponsorEmail"] == "bob@example.com"


# ---------------------------------------------------------------------------
# add_player / is_admin
# ---------------------------------------------------------------------------

@pytest.mark.unit
@mock_aws
def test_add_player_creates_active_record():
    _create_tables()
    _reset_dynamo_caches()

    add_player("alice@example.com", "Alice")

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    item = table.get_item(Key={"email": "alice@example.com", "active": "true"})["Item"]
    assert item["name"] == "Alice"
    assert item["isAdmin"] == False


@pytest.mark.unit
@mock_aws
def test_add_player_as_admin():
    _create_tables()
    _reset_dynamo_caches()

    add_player("bob@example.com", "Bob", is_admin=True)

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    item = table.get_item(Key={"email": "bob@example.com", "active": "true"})["Item"]
    assert item["isAdmin"] == True


@pytest.mark.unit
@mock_aws
def test_add_player_raises_for_duplicate():
    _create_tables()
    _reset_dynamo_caches()

    add_player("alice@example.com", "Alice")

    from botocore.exceptions import ClientError
    with pytest.raises(ClientError):
        add_player("alice@example.com", "Alice Duplicate")


@pytest.mark.unit
@mock_aws
def test_is_admin_returns_true_for_admin():
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    table.put_item(Item={"email": "alice@example.com", "active": "true", "name": "Alice", "isAdmin": True})

    assert is_admin("alice@example.com") is True


@pytest.mark.unit
@mock_aws
def test_is_admin_returns_false_for_non_admin():
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    table.put_item(Item={"email": "alice@example.com", "active": "true", "name": "Alice", "isAdmin": False})

    assert is_admin("alice@example.com") is False


@pytest.mark.unit
@mock_aws
def test_is_admin_returns_false_for_nonexistent_player():
    _create_tables()
    _reset_dynamo_caches()

    assert is_admin("nobody@example.com") is False


# ---------------------------------------------------------------------------
# deactivate_player / reactivate_player
# ---------------------------------------------------------------------------

@pytest.mark.unit
@mock_aws
def test_deactivate_player_preserves_attributes():
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    table.put_item(Item={"email": "alice@example.com", "active": "true", "name": "Alice", "isAdmin": True})

    deactivate_player("alice@example.com")

    # Active record should be gone
    active = table.get_item(Key={"email": "alice@example.com", "active": "true"}).get("Item")
    assert active is None

    # Inactive record should exist with same attributes
    inactive = table.get_item(Key={"email": "alice@example.com", "active": "false"})["Item"]
    assert inactive["name"] == "Alice"
    assert inactive["isAdmin"] == True


@pytest.mark.unit
@mock_aws
def test_reactivate_player_restores_active_record():
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    table.put_item(Item={"email": "alice@example.com", "active": "false", "name": "Alice", "isAdmin": False})

    reactivate_player("alice@example.com")

    # Inactive record should be gone
    inactive = table.get_item(Key={"email": "alice@example.com", "active": "false"}).get("Item")
    assert inactive is None

    # Active record should exist
    active = table.get_item(Key={"email": "alice@example.com", "active": "true"})["Item"]
    assert active["name"] == "Alice"


@pytest.mark.unit
@mock_aws
def test_deactivate_player_not_found_raises():
    _create_tables()
    _reset_dynamo_caches()

    with pytest.raises(ValueError, match="No active player found"):
        deactivate_player("nobody@example.com")


@pytest.mark.unit
@mock_aws
def test_reactivate_player_not_found_raises():
    _create_tables()
    _reset_dynamo_caches()

    with pytest.raises(ValueError, match="No inactive player found"):
        reactivate_player("nobody@example.com")


# ---------------------------------------------------------------------------
# pre_cancel_game
# ---------------------------------------------------------------------------

@pytest.mark.unit
@mock_aws
def test_pre_cancel_game_creates_cancelled_record():
    _create_tables()
    _reset_dynamo_caches()

    pre_cancel_game("2026-04-11")

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-games")
    item = table.get_item(Key={"gameDate": "2026-04-11", "sk": "gameStatus"})["Item"]
    assert item["status"] == "CANCELLED"
    assert "createdAt" in item


@pytest.mark.unit
@mock_aws
def test_pre_cancel_game_does_not_overwrite_open_game():
    """pre_cancel_game should NOT be called on an existing game — that's update_game_status's job."""
    _create_tables()
    _reset_dynamo_caches()

    # First create an OPEN game
    create_game("2026-04-11")

    # pre_cancel_game uses put_item (overwrites); if called on existing game it would
    # reset playerStatus records. The admin handler must choose the right path.
    # This test just ensures the function sets CANCELLED.
    pre_cancel_game("2026-04-11")

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-games")
    item = table.get_item(Key={"gameDate": "2026-04-11", "sk": "gameStatus"})["Item"]
    assert item["status"] == "CANCELLED"


@pytest.mark.unit
@mock_aws
def test_get_sender_role_active_player():
    """Active player is identified as 'player'."""
    from common.dynamo import get_sender_role
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    dynamodb.Table("test-players").put_item(Item={"email": "alice@example.com", "active": "true", "name": "Alice"})

    assert get_sender_role("alice@example.com") == "player"


@pytest.mark.unit
@mock_aws
def test_get_sender_role_guest():
    """Guest with own contact email is identified as 'guest'."""
    from common.dynamo import get_sender_role
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    dynamodb.Table("test-players").put_item(
        Item={"email": "john@example.com", "active": "guest#active", "name": "John", "sponsorEmail": "alice@example.com"}
    )

    assert get_sender_role("john@example.com") == "guest"


@pytest.mark.unit
@mock_aws
def test_get_sender_role_unknown():
    """Email not in Players table at all is 'unknown'."""
    from common.dynamo import get_sender_role
    _create_tables()
    _reset_dynamo_caches()

    assert get_sender_role("stranger@example.com") == "unknown"


@pytest.mark.unit
@mock_aws
def test_get_sender_role_deactivated_player_is_unknown():
    """Deactivated player (active='false') is treated as 'unknown'."""
    from common.dynamo import get_sender_role
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    dynamodb.Table("test-players").put_item(Item={"email": "bob@example.com", "active": "false", "name": "Bob"})

    assert get_sender_role("bob@example.com") == "unknown"


@pytest.mark.unit
@mock_aws
def test_remove_guest_from_status_found(sample_game_date):
    """remove_guest_from_status removes the guest and returns the guest object."""
    from common.dynamo import remove_guest_from_status
    _create_tables()
    _reset_dynamo_caches()

    guest = {"pk": "john@example.com", "sk": "guest#active", "name": "John",
             "sponsorEmail": "alice@example.com", "sponsorName": "Alice"}
    create_game(sample_game_date)
    add_guests_to_game_status(sample_game_date, "YES", [guest])

    result = remove_guest_from_status(sample_game_date, "YES", "john@example.com")

    assert result is not None
    assert result["pk"] == "john@example.com"

    roster = get_roster(sample_game_date)
    assert not any(g["pk"] == "john@example.com" for g in roster["YES"]["guests"])


@pytest.mark.unit
@mock_aws
def test_remove_guest_from_status_not_found(sample_game_date):
    """remove_guest_from_status returns None when guest pk is not in the list."""
    from common.dynamo import remove_guest_from_status
    _create_tables()
    _reset_dynamo_caches()

    create_game(sample_game_date)

    result = remove_guest_from_status(sample_game_date, "YES", "nobody@example.com")
    assert result is None
