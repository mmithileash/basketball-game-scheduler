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

    Returns: {"YES": {email: {"guests": [...]}}, "NO": {...}, "MAYBE": {...}}
    """
    config = _get_config()
    table = _get_resource().Table(config.games_table)

    response = table.query(
        KeyConditionExpression=(
            Key("gameDate").eq(game_date)
            & Key("sk").begins_with("playerStatus#")
        )
    )

    roster: dict[str, dict[str, Any]] = {"YES": {}, "NO": {}, "MAYBE": {}}
    for item in response.get("Items", []):
        sk = item["sk"]
        status = sk.split("#", 1)[1]  # YES, NO, or MAYBE
        players_map = item.get("players", {})
        for email, data in players_map.items():
            roster[status][email] = {"guests": data.get("guests", [])}

    logger.info("Roster for %s: YES=%d, NO=%d, MAYBE=%d",
                game_date, len(roster["YES"]), len(roster["NO"]), len(roster["MAYBE"]))
    return roster


def update_player_response(
    game_date: str,
    email: str,
    new_status: str,
    guests: list[str] | None = None,
    old_status: str | None = None,
) -> None:
    """Update a player's response status.

    If old_status is provided, uses TransactWriteItems to atomically remove
    from old status and add to new status. Otherwise, just sets on new status.
    """
    config = _get_config()
    client = _get_client()
    guest_list = guests or []

    # Build the guest value for DynamoDB
    guest_value = {"L": [{"S": g} for g in guest_list]}
    player_value = {"M": {"guests": guest_value}}

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
        logger.info("Moved %s from %s to %s for game %s",
                     email, old_status, new_status, game_date)
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
        logger.info("Set %s to %s for game %s", email, new_status, game_date)


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


def get_pending_players(game_date: str) -> list[dict[str, Any]]:
    """Get active players who haven't responded to the game."""
    active_players = get_active_players()
    roster = get_roster(game_date)

    responded_emails: set[str] = set()
    for status_players in roster.values():
        responded_emails.update(status_players.keys())

    pending = [p for p in active_players if p["email"] not in responded_emails]
    logger.info("Found %d pending players for game %s", len(pending), game_date)
    return pending
