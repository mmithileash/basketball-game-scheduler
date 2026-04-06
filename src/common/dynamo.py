import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from common.config import load_config

logger = logging.getLogger(__name__)

_config = None
_dynamodb = None
_client = None


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_resource():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("dynamodb")
    return _client


def get_active_players() -> list[dict[str, Any]]:
    """Scan Players table for active players (SK='true')."""
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    response = table.scan(
        FilterExpression="active = :val",
        ExpressionAttributeValues={":val": "true"},
    )
    items = response.get("Items", [])

    # Handle pagination
    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression="active = :val",
            ExpressionAttributeValues={":val": "true"},
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    logger.info("Found %d active players", len(items))
    return [{"email": item["email"], "name": item.get("name")} for item in items]


def create_game(game_date: str) -> None:
    """Create a new game with status OPEN and empty player status items."""
    config = _get_config()
    client = _get_client()
    now = datetime.now(timezone.utc).isoformat()

    items = [
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "gameStatus"},
                    "status": {"S": "OPEN"},
                    "createdAt": {"S": now},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "playerStatus#YES"},
                    "players": {"M": {}},
                    "guests": {"L": []},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "playerStatus#NO"},
                    "players": {"M": {}},
                    "guests": {"L": []},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "playerStatus#MAYBE"},
                    "players": {"M": {}},
                    "guests": {"L": []},
                }
            }
        },
    ]

    client.batch_write_item(RequestItems={config.games_table: items})
    logger.info("Created game for %s", game_date)


def get_game_status(game_date: str) -> dict[str, Any] | None:
    """Get the gameStatus item for a given date."""
    config = _get_config()
    table = _get_resource().Table(config.games_table)

    response = table.get_item(
        Key={"gameDate": game_date, "sk": "gameStatus"}
    )
    item = response.get("Item")
    if item:
        logger.info("Game %s status: %s", game_date, item.get("status"))
    else:
        logger.info("No game found for %s", game_date)
    return item


def get_roster(game_date: str) -> dict[str, dict[str, Any]]:
    """Query all playerStatus# items for a game date.

    Returns: {
        "YES": {"players": {email: {"name": str}}, "guests": [guest_obj]},
        "NO":  {"players": {...}, "guests": [...]},
        "MAYBE": {"players": {...}, "guests": [...]},
    }
    """
    config = _get_config()
    table = _get_resource().Table(config.games_table)

    response = table.query(
        KeyConditionExpression=(
            Key("gameDate").eq(game_date)
            & Key("sk").begins_with("playerStatus#")
        )
    )

    roster: dict[str, dict[str, Any]] = {
        "YES": {"players": {}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    }
    for item in response.get("Items", []):
        status = item["sk"].split("#", 1)[1]
        roster[status]["players"] = {
            email: {"name": data.get("name")}
            for email, data in item.get("players", {}).items()
        }
        roster[status]["guests"] = list(item.get("guests", []))

    logger.info(
        f"Roster for {game_date}: YES={len(roster['YES']['players'])} players "
        f"+ {len(roster['YES']['guests'])} guests"
    )
    return roster


def update_player_response(
    game_date: str,
    email: str,
    new_status: str,
    name: str | None = None,
    old_status: str | None = None,
) -> None:
    """Update a player's response status.

    If old_status is provided, uses TransactWriteItems to atomically remove
    from old status and add to new status. Otherwise, just sets on new status.
    """
    config = _get_config()
    client = _get_client()

    player_value = {"M": {"name": {"S": name or ""}}}
    new_sk = f"playerStatus#{new_status}"

    if old_status and old_status != new_status:
        old_sk = f"playerStatus#{old_status}"
        client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": config.games_table,
                        "Key": {
                            "gameDate": {"S": game_date},
                            "sk": {"S": old_sk},
                        },
                        "UpdateExpression": "REMOVE players.#email",
                        "ExpressionAttributeNames": {"#email": email},
                    }
                },
                {
                    "Update": {
                        "TableName": config.games_table,
                        "Key": {
                            "gameDate": {"S": game_date},
                            "sk": {"S": new_sk},
                        },
                        "UpdateExpression": "SET players.#email = :val",
                        "ExpressionAttributeNames": {"#email": email},
                        "ExpressionAttributeValues": {":val": player_value},
                    }
                },
            ]
        )
        logger.info(f"Moved {email} from {old_status} to {new_status} for game {game_date}")
    else:
        client.update_item(
            TableName=config.games_table,
            Key={
                "gameDate": {"S": game_date},
                "sk": {"S": new_sk},
            },
            UpdateExpression="SET players.#email = :val",
            ExpressionAttributeNames={"#email": email},
            ExpressionAttributeValues={":val": player_value},
        )
        logger.info(f"Set {email} to {new_status} for game {game_date}")


def update_game_status(game_date: str, status: str) -> None:
    """Update the gameStatus item's status field."""
    config = _get_config()
    table = _get_resource().Table(config.games_table)

    table.update_item(
        Key={"gameDate": game_date, "sk": "gameStatus"},
        UpdateExpression="SET #status = :val",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":val": status},
    )
    logger.info("Updated game %s status to %s", game_date, status)


def _next_saturday(today: date) -> date:
    """Return the date of the coming Saturday (or today if today is Saturday)."""
    days_ahead = (5 - today.weekday()) % 7
    return today + timedelta(days=days_ahead)


def get_current_open_game() -> dict[str, Any] | None:
    """Get the open game for the upcoming Saturday via a direct point read."""
    saturday = _next_saturday(date.today())
    game_date = saturday.isoformat()
    item = get_game_status(game_date)
    if item and item.get("status") == "OPEN":
        logger.info(f"Current open game: {game_date}")
        return item
    logger.info(f"No open game found for upcoming Saturday {game_date}")
    return None


def get_upcoming_game() -> dict[str, Any] | None:
    """Get the gameStatus item for the upcoming Saturday regardless of status.

    Returns the raw game record (with the ``status`` field intact, e.g. OPEN /
    CANCELLED / PLAYED) or None if no record exists for that date. Callers that
    only want games still accepting RSVPs should use ``get_current_open_game``
    instead; callers that need to react differently to a cancelled game (e.g.
    the email processor) should use this function and branch on ``status``.
    """
    saturday = _next_saturday(date.today())
    game_date = saturday.isoformat()
    return get_game_status(game_date)


def get_pending_players(game_date: str) -> list[dict[str, Any]]:
    """Get active players who haven't responded to the game."""
    active_players = get_active_players()
    roster = get_roster(game_date)

    responded_emails: set[str] = set()
    for status_data in roster.values():
        responded_emails.update(status_data["players"].keys())

    pending = [p for p in active_players if p["email"] not in responded_emails]
    logger.info("Found %d pending players for game %s", len(pending), game_date)
    return pending


def get_player_name(email: str) -> str | None:
    """Get the name of an active player from the Players table."""
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    response = table.get_item(Key={"email": email, "active": "true"})
    item = response.get("Item")
    if item:
        return item.get("name") or None
    return None


def _guest_to_ddb(g: dict[str, Any]) -> dict[str, Any]:
    """Convert a guest object dict to DynamoDB wire format."""
    return {"M": {
        "pk": {"S": g["pk"]},
        "sk": {"S": g["sk"]},
        "name": {"S": g["name"]},
        "sponsorEmail": {"S": g["sponsorEmail"]},
        "sponsorName": {"S": g["sponsorName"]},
    }}


def create_guest_entry(
    game_date: str,
    guest_name: str,
    sponsor_email: str,
    sponsor_name: str,
    contact_email: str | None = None,
) -> dict[str, Any]:
    """Create a guest entry in the Players table.

    Returns the guest object {pk, sk, name, sponsorEmail, sponsorName}
    to be stored in the Games table guests list.
    """
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    if contact_email:
        pk = contact_email
        sk = "guest#active"
    else:
        pk = sponsor_email
        sk = f"guest#active#{guest_name}"

    table.put_item(Item={
        "email": pk,
        "active": sk,
        "name": guest_name,
        "sponsorEmail": sponsor_email,
        "gameDate": game_date,
    })

    guest_obj = {
        "pk": pk,
        "sk": sk,
        "name": guest_name,
        "sponsorEmail": sponsor_email,
        "sponsorName": sponsor_name,
    }
    logger.info(f"Created guest entry for {guest_name} (sponsor: {sponsor_email})")
    return guest_obj


def delete_guest_entries(guest_objects: list[dict[str, Any]]) -> None:
    """Delete guest entries from the Players table by their pk/sk pairs."""
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    for guest in guest_objects:
        table.delete_item(Key={"email": guest["pk"], "active": guest["sk"]})
        logger.info(f"Deleted guest entry pk={guest['pk']} sk={guest['sk']}")


def add_guests_to_game_status(
    game_date: str,
    status: str,
    guests: list[dict[str, Any]],
) -> None:
    """Append guest objects to the guests list on a playerStatus item."""
    if not guests:
        return

    config = _get_config()
    client = _get_client()

    guest_list = [_guest_to_ddb(g) for g in guests]

    client.update_item(
        TableName=config.games_table,
        Key={"gameDate": {"S": game_date}, "sk": {"S": f"playerStatus#{status}"}},
        UpdateExpression="SET #guests = list_append(if_not_exists(#guests, :empty), :new)",
        ExpressionAttributeNames={"#guests": "guests"},
        ExpressionAttributeValues={
            ":new": {"L": guest_list},
            ":empty": {"L": []},
        },
    )
    logger.info(f"Added {len(guests)} guest(s) to playerStatus#{status} for {game_date}")


def remove_sponsor_guests_from_status(
    game_date: str,
    status: str,
    sponsor_email: str,
) -> list[dict[str, Any]]:
    """Remove and return all guest objects for a sponsor from a playerStatus item.

    Reads the current guests list, filters out the sponsor's guests,
    writes back the remaining list, and returns the removed guest objects.
    """
    config = _get_config()
    table = _get_resource().Table(config.games_table)
    client = _get_client()

    response = table.get_item(
        Key={"gameDate": game_date, "sk": f"playerStatus#{status}"}
    )
    item = response.get("Item", {})
    all_guests: list[dict[str, Any]] = list(item.get("guests", []))

    sponsor_guests = [g for g in all_guests if g.get("sponsorEmail") == sponsor_email]
    remaining = [g for g in all_guests if g.get("sponsorEmail") != sponsor_email]

    remaining_ddb = [_guest_to_ddb(g) for g in remaining]

    client.update_item(
        TableName=config.games_table,
        Key={"gameDate": {"S": game_date}, "sk": {"S": f"playerStatus#{status}"}},
        UpdateExpression="SET #guests = :remaining",
        ExpressionAttributeNames={"#guests": "guests"},
        ExpressionAttributeValues={":remaining": {"L": remaining_ddb}},
    )
    logger.info(
        f"Removed {len(sponsor_guests)} guest(s) for {sponsor_email} "
        f"from playerStatus#{status} for {game_date}"
    )
    return sponsor_guests


def move_confirmed_guests(
    game_date: str,
    sponsor_email: str,
    confirmed_names: list[str],
) -> None:
    """Move named guests from playerStatus#NO to playerStatus#YES.

    Only guests matching confirmed_names (by name) and sponsorEmail are moved.
    Remaining guests stay in NO.
    """
    config = _get_config()
    table = _get_resource().Table(config.games_table)
    client = _get_client()

    response = table.get_item(
        Key={"gameDate": game_date, "sk": "playerStatus#NO"}
    )
    item = response.get("Item", {})
    no_guests: list[dict[str, Any]] = list(item.get("guests", []))

    confirmed_set = set(confirmed_names)
    to_move = [
        g for g in no_guests
        if g.get("sponsorEmail") == sponsor_email and g.get("name") in confirmed_set
    ]
    remaining_no = [g for g in no_guests if g not in to_move]

    if not to_move:
        logger.info(f"No matching guests to move for {sponsor_email}, names={confirmed_names}")
        return

    remaining_ddb = [_guest_to_ddb(g) for g in remaining_no]
    to_move_ddb = [_guest_to_ddb(g) for g in to_move]

    client.transact_write_items(TransactItems=[
        {
            "Update": {
                "TableName": config.games_table,
                "Key": {"gameDate": {"S": game_date}, "sk": {"S": "playerStatus#NO"}},
                "UpdateExpression": "SET #guests = :remaining",
                "ExpressionAttributeNames": {"#guests": "guests"},
                "ExpressionAttributeValues": {":remaining": {"L": remaining_ddb}},
            }
        },
        {
            "Update": {
                "TableName": config.games_table,
                "Key": {"gameDate": {"S": game_date}, "sk": {"S": "playerStatus#YES"}},
                "UpdateExpression": "SET #guests = list_append(if_not_exists(#guests, :empty), :moving)",
                "ExpressionAttributeNames": {"#guests": "guests"},
                "ExpressionAttributeValues": {
                    ":moving": {"L": to_move_ddb},
                    ":empty": {"L": []},
                },
            }
        },
    ])
    logger.info(f"Moved {len(to_move)} guest(s) from NO to YES for {sponsor_email}")
