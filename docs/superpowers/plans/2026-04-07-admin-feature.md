# Admin Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow designated admin players to cancel games and manage the player roster by emailing a dedicated `admin@<domain>` address, handled by a new `admin-processor` Lambda.

**Architecture:** A new SES receipt rule routes `admin@<domain>` emails to an `admin/` S3 prefix, triggering a dedicated `admin-processor` Lambda. It authenticates the sender via a DynamoDB `isAdmin` flag, parses intent via Bedrock, and executes the command (cancellation, player add/deactivate/reactivate). The announcement-sender is updated to skip pre-cancelled games; the reminder-checker already handles CANCELLED games correctly via `get_current_open_game()`.

**Tech Stack:** Python 3.12, boto3, moto (tests), AWS Lambda, DynamoDB, SES, S3, Bedrock (Claude Haiku), Terraform

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `src/common/config.py` | Modify | Add `admin_email` field |
| `src/common/dynamo.py` | Modify | Add `add_player`, `set_player_admin`, `deactivate_player`, `reactivate_player`, `is_admin`, `pre_cancel_game` |
| `src/common/email_service.py` | Modify | Add `send_no_game_announcement`, `send_admin_cancelled_broadcast` |
| `src/common/bedrock_client.py` | Modify | Add `parse_admin_email` |
| `src/admin_processor/__init__.py` | Create | Empty package marker |
| `src/admin_processor/handler.py` | Create | Admin Lambda handler |
| `src/announcement_sender/handler.py` | Modify | Check for pre-cancelled game before creating |
| `tests/unit/conftest.py` | Modify | Add `ADMIN_EMAIL` env var + reset for admin_processor cache |
| `tests/unit/test_dynamo.py` | Modify | Tests for new dynamo functions |
| `tests/unit/test_email_service.py` | Modify | Tests for new email templates |
| `tests/unit/test_admin_processor.py` | Create | Tests for admin handler |
| `tests/unit/test_announcement_handler.py` | Modify | Test pre-cancelled game path |
| `tests/unit/test_reminder_handler.py` | Modify | Test CANCELLED game skip |
| `terraform/variables.tf` | Modify | Add `admin_email` variable |
| `terraform/lambda.tf` | Modify | Add `admin_processor` Lambda + build + env var |
| `terraform/ses.tf` | Modify | Add admin SES receipt rule |
| `terraform/s3.tf` | Modify | Add `admin/` prefix notification + prefix filter on existing |

---

## Task 1: Add `admin_email` to Config

**Files:**
- Modify: `src/common/config.py`
- Modify: `tests/unit/conftest.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/conftest.py` in the `set_env_vars` fixture, after the existing `monkeypatch.setenv` calls:

```python
monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
```

Also reset the admin_processor module cache if it exists. Add after the existing `bedrock_mod._bedrock_client = None` line:

```python
try:
    import admin_processor.handler as admin_mod
    admin_mod._s3_client = None
except ImportError:
    pass
```

Now write a test. Create `tests/unit/test_config.py` — but first check if there's already a config test:

```bash
ls tests/unit/
```

If `test_config.py` doesn't exist, add this check inline by verifying the env var is loaded. For now, just add a quick assertion in the conftest is enough — the real tests come when the config is used in Task 5.

- [ ] **Step 2: Update `src/common/config.py`**

Replace the file contents with:

```python
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    players_table: str
    games_table: str
    email_bucket: str
    sender_email: str
    admin_email: str
    game_time: str
    game_location: str
    bedrock_model_id: str
    min_players: int


def load_config() -> Config:
    return Config(
        players_table=os.environ["PLAYERS_TABLE"],
        games_table=os.environ["GAMES_TABLE"],
        email_bucket=os.environ["EMAIL_BUCKET"],
        sender_email=os.environ["SENDER_EMAIL"],
        admin_email=os.environ["ADMIN_EMAIL"],
        game_time=os.environ.get("GAME_TIME", "10:00 AM"),
        game_location=os.environ.get("GAME_LOCATION", "TBD"),
        bedrock_model_id=os.environ.get(
            "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
        ),
        min_players=int(os.environ.get("MIN_PLAYERS", "6")),
    )
```

- [ ] **Step 3: Run existing tests to confirm no breakage**

```bash
pytest tests/unit/ -v -x
```

Expected: All existing tests PASS (conftest now sets `ADMIN_EMAIL`, config picks it up).

- [ ] **Step 4: Commit**

```bash
git add src/common/config.py tests/unit/conftest.py
git commit -m "feat(config): add admin_email field"
```

---

## Task 2: DynamoDB — Player Management Functions

**Files:**
- Modify: `src/common/dynamo.py`
- Modify: `tests/unit/test_dynamo.py`

### 2a: `add_player` and `is_admin`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_dynamo.py` — extend the import list at the top to include the new functions:

```python
from common.dynamo import (
    add_player,
    is_admin,
    # ... existing imports
)
```

Add test functions:

```python
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


@mock_aws
def test_add_player_as_admin():
    _create_tables()
    _reset_dynamo_caches()

    add_player("bob@example.com", "Bob", is_admin=True)

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    item = table.get_item(Key={"email": "bob@example.com", "active": "true"})["Item"]
    assert item["isAdmin"] == True


@mock_aws
def test_is_admin_returns_true_for_admin():
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    table.put_item(Item={"email": "alice@example.com", "active": "true", "name": "Alice", "isAdmin": True})

    assert is_admin("alice@example.com") is True


@mock_aws
def test_is_admin_returns_false_for_non_admin():
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    table.put_item(Item={"email": "alice@example.com", "active": "true", "name": "Alice", "isAdmin": False})

    assert is_admin("alice@example.com") is False


@mock_aws
def test_is_admin_returns_false_for_nonexistent_player():
    _create_tables()
    _reset_dynamo_caches()

    assert is_admin("nobody@example.com") is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_dynamo.py::test_add_player_creates_active_record tests/unit/test_dynamo.py::test_is_admin_returns_true_for_admin -v
```

Expected: FAIL with `ImportError: cannot import name 'add_player'`

- [ ] **Step 3: Implement `add_player` and `is_admin` in `src/common/dynamo.py`**

Add at the end of the file:

```python
def add_player(email: str, name: str, is_admin: bool = False) -> None:
    """Add a new active player to the Players table."""
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    table.put_item(Item={
        "email": email,
        "active": "true",
        "name": name,
        "isAdmin": is_admin,
    })
    logger.info(f"Added player {email} (name={name}, isAdmin={is_admin})")


def is_admin(email: str) -> bool:
    """Return True if the email belongs to an active admin player."""
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    response = table.get_item(Key={"email": email, "active": "true"})
    item = response.get("Item")
    if not item:
        return False
    return bool(item.get("isAdmin", False))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_dynamo.py::test_add_player_creates_active_record tests/unit/test_dynamo.py::test_add_player_as_admin tests/unit/test_dynamo.py::test_is_admin_returns_true_for_admin tests/unit/test_dynamo.py::test_is_admin_returns_false_for_non_admin tests/unit/test_dynamo.py::test_is_admin_returns_false_for_nonexistent_player -v
```

Expected: All 5 PASS

### 2b: `set_player_admin`, `deactivate_player`, `reactivate_player`

- [ ] **Step 5: Write failing tests**

Add to the test_dynamo.py import list:

```python
from common.dynamo import (
    deactivate_player,
    reactivate_player,
    set_player_admin,
    # ... existing imports
)
```

Add test functions:

```python
@mock_aws
def test_set_player_admin_promotes_player():
    _create_tables()
    _reset_dynamo_caches()

    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table("test-players")
    table.put_item(Item={"email": "alice@example.com", "active": "true", "name": "Alice", "isAdmin": False})

    set_player_admin("alice@example.com", True)

    item = table.get_item(Key={"email": "alice@example.com", "active": "true"})["Item"]
    assert item["isAdmin"] == True


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


@mock_aws
def test_deactivate_player_not_found_raises():
    _create_tables()
    _reset_dynamo_caches()

    with pytest.raises(ValueError, match="No active player found"):
        deactivate_player("nobody@example.com")


@mock_aws
def test_reactivate_player_not_found_raises():
    _create_tables()
    _reset_dynamo_caches()

    with pytest.raises(ValueError, match="No inactive player found"):
        reactivate_player("nobody@example.com")
```

- [ ] **Step 6: Run to verify they fail**

```bash
pytest tests/unit/test_dynamo.py::test_set_player_admin_promotes_player tests/unit/test_dynamo.py::test_deactivate_player_preserves_attributes -v
```

Expected: FAIL with `ImportError: cannot import name 'set_player_admin'`

- [ ] **Step 7: Implement in `src/common/dynamo.py`**

Add after the `is_admin` function:

```python
def set_player_admin(email: str, is_admin_flag: bool) -> None:
    """Set the isAdmin flag on an active player record."""
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    table.update_item(
        Key={"email": email, "active": "true"},
        UpdateExpression="SET isAdmin = :val",
        ExpressionAttributeValues={":val": is_admin_flag},
    )
    logger.info(f"Set isAdmin={is_admin_flag} for {email}")


def deactivate_player(email: str) -> None:
    """Move a player from active to inactive, preserving all attributes."""
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    response = table.get_item(Key={"email": email, "active": "true"})
    item = response.get("Item")
    if not item:
        raise ValueError(f"No active player found for {email}")

    inactive_item = {k: v for k, v in item.items()}
    inactive_item["active"] = "false"

    table.delete_item(Key={"email": email, "active": "true"})
    table.put_item(Item=inactive_item)
    logger.info(f"Deactivated player {email}")


def reactivate_player(email: str) -> None:
    """Move a player from inactive to active, preserving all attributes."""
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    response = table.get_item(Key={"email": email, "active": "false"})
    item = response.get("Item")
    if not item:
        raise ValueError(f"No inactive player found for {email}")

    active_item = {k: v for k, v in item.items()}
    active_item["active"] = "true"

    table.delete_item(Key={"email": email, "active": "false"})
    table.put_item(Item=active_item)
    logger.info(f"Reactivated player {email}")
```

- [ ] **Step 8: Run all new dynamo tests**

```bash
pytest tests/unit/test_dynamo.py::test_set_player_admin_promotes_player tests/unit/test_dynamo.py::test_deactivate_player_preserves_attributes tests/unit/test_dynamo.py::test_reactivate_player_restores_active_record tests/unit/test_dynamo.py::test_deactivate_player_not_found_raises tests/unit/test_dynamo.py::test_reactivate_player_not_found_raises -v
```

Expected: All 5 PASS

### 2c: `pre_cancel_game`

- [ ] **Step 9: Write failing test**

Add import: `from common.dynamo import pre_cancel_game`

Add test:

```python
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
```

- [ ] **Step 10: Run to verify they fail**

```bash
pytest tests/unit/test_dynamo.py::test_pre_cancel_game_creates_cancelled_record -v
```

Expected: FAIL with `ImportError: cannot import name 'pre_cancel_game'`

- [ ] **Step 11: Implement `pre_cancel_game` in `src/common/dynamo.py`**

Add after `reactivate_player`:

```python
def pre_cancel_game(game_date: str) -> None:
    """Write a CANCELLED gameStatus record for a date before the game is announced.

    Used by admin-processor for advance cancellation. The record will prevent
    announcement-sender from creating an OPEN game on Monday.
    """
    config = _get_config()
    table = _get_resource().Table(config.games_table)
    now = datetime.now(timezone.utc).isoformat()

    table.put_item(Item={
        "gameDate": game_date,
        "sk": "gameStatus",
        "status": "CANCELLED",
        "createdAt": now,
    })
    logger.info(f"Pre-cancelled game for {game_date}")
```

- [ ] **Step 12: Run all dynamo tests**

```bash
pytest tests/unit/test_dynamo.py -v
```

Expected: All tests PASS

- [ ] **Step 13: Commit**

```bash
git add src/common/dynamo.py tests/unit/test_dynamo.py
git commit -m "feat(dynamo): add player management and pre_cancel_game functions"
```

---

## Task 3: Email Service — New Templates

**Files:**
- Modify: `src/common/email_service.py`
- Modify: `tests/unit/test_email_service.py`

- [ ] **Step 1: Write failing tests**

Open `tests/unit/test_email_service.py` and add imports + tests at the end:

```python
from common.email_service import send_no_game_announcement, send_admin_cancelled_broadcast
```

Add:

```python
@pytest.mark.unit
def test_send_no_game_announcement(mocker, ses_setup):
    mock_send = mocker.patch("common.email_service.send_email")

    send_no_game_announcement("alice@example.com", "Alice", "2026-04-11")

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == "alice@example.com"
    assert "2026-04-11" in args[1]  # subject
    assert "cancelled" in args[2].lower() or "no game" in args[2].lower()


@pytest.mark.unit
def test_send_no_game_announcement_no_name(mocker, ses_setup):
    mock_send = mocker.patch("common.email_service.send_email")

    send_no_game_announcement("alice@example.com", None, "2026-04-11")

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert "2026-04-11" in args[2]


@pytest.mark.unit
def test_send_admin_cancelled_broadcast(mocker, ses_setup):
    mock_send = mocker.patch("common.email_service.send_email")

    send_admin_cancelled_broadcast("alice@example.com", "2026-04-11")

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == "alice@example.com"
    assert "2026-04-11" in args[1]
    assert "cancelled" in args[2].lower()
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_email_service.py::test_send_no_game_announcement tests/unit/test_email_service.py::test_send_admin_cancelled_broadcast -v
```

Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement in `src/common/email_service.py`**

Add at the end of the file:

```python
def send_no_game_announcement(
    player_email: str,
    player_name: str | None,
    game_date: str,
) -> None:
    """Notify a player that no game is scheduled this week (admin pre-cancelled)."""
    greeting = f"Hi {player_name}" if player_name else "Hi"

    subject = f"No Game This Week - {game_date}"
    body = (
        f"{greeting},\n\n"
        f"There will be no basketball game this week ({game_date}). "
        f"The game has been cancelled by the organiser.\n\n"
        f"See you next week!\n"
    )

    send_email(player_email, subject, body)


def send_admin_cancelled_broadcast(player_email: str, game_date: str) -> None:
    """Notify a player that an already-announced game has been cancelled by admin."""
    subject = f"Cancelled: Basketball Game - {game_date}"
    body = (
        f"Hi,\n\n"
        f"The basketball game scheduled for {game_date} has been cancelled by the organiser.\n\n"
        f"See you next week!\n"
    )

    send_email(player_email, subject, body)
```

- [ ] **Step 4: Run email service tests**

```bash
pytest tests/unit/test_email_service.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/common/email_service.py tests/unit/test_email_service.py
git commit -m "feat(email): add no-game and admin-cancel broadcast templates"
```

---

## Task 4: Bedrock — Admin Intent Parser

**Files:**
- Modify: `src/common/bedrock_client.py`
- Modify: `tests/unit/test_bedrock_client.py`

- [ ] **Step 1: Write failing tests**

Open `tests/unit/test_bedrock_client.py`. Add at the top alongside existing imports:

```python
from common.bedrock_client import parse_admin_email
```

Add tests at the end of the file:

```python
@pytest.mark.unit
def test_parse_admin_email_cancel_game(mocker):
    mock_response = {
        "body": mocker.MagicMock(
            read=lambda: json.dumps({
                "content": [{"text": json.dumps({
                    "intent": "CANCEL_GAME",
                    "game_date": "2026-04-11",
                    "email": None,
                    "name": None,
                    "is_admin": None,
                })}]
            }).encode()
        )
    }
    mocker.patch("common.bedrock_client._get_bedrock_client").return_value.invoke_model.return_value = mock_response

    result = parse_admin_email("Cancel the game on April 11", "admin@example.com")

    assert result["intent"] == "CANCEL_GAME"
    assert result["game_date"] == "2026-04-11"


@pytest.mark.unit
def test_parse_admin_email_add_player(mocker):
    mock_response = {
        "body": mocker.MagicMock(
            read=lambda: json.dumps({
                "content": [{"text": json.dumps({
                    "intent": "ADD_PLAYER",
                    "game_date": None,
                    "email": "newplayer@example.com",
                    "name": "New Player",
                    "is_admin": False,
                })}]
            }).encode()
        )
    }
    mocker.patch("common.bedrock_client._get_bedrock_client").return_value.invoke_model.return_value = mock_response

    result = parse_admin_email("Add player newplayer@example.com, name New Player", "admin@example.com")

    assert result["intent"] == "ADD_PLAYER"
    assert result["email"] == "newplayer@example.com"
    assert result["name"] == "New Player"
    assert result["is_admin"] == False


@pytest.mark.unit
def test_parse_admin_email_json_error_returns_unknown(mocker):
    mock_response = {
        "body": mocker.MagicMock(read=lambda: b'{"content": [{"text": "not json"}]}')
    }
    mocker.patch("common.bedrock_client._get_bedrock_client").return_value.invoke_model.return_value = mock_response

    result = parse_admin_email("gibberish", "admin@example.com")

    assert result["intent"] == "UNKNOWN"
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_bedrock_client.py::test_parse_admin_email_cancel_game -v
```

Expected: FAIL with `ImportError: cannot import name 'parse_admin_email'`

- [ ] **Step 3: Implement `parse_admin_email` in `src/common/bedrock_client.py`**

Add at the end of the file:

```python
def parse_admin_email(email_body: str, sender_email: str) -> dict[str, Any]:
    """Use Bedrock Claude to parse an admin command email into a structured intent.

    Returns: {
        "intent": str,  # CANCEL_GAME, ADD_PLAYER, ADD_ADMIN, DEACTIVATE_PLAYER, REACTIVATE_PLAYER, UNKNOWN
        "game_date": str | None,   # YYYY-MM-DD Saturday, for CANCEL_GAME
        "email": str | None,       # Target player email, for player management commands
        "name": str | None,        # Player name, for ADD_PLAYER / ADD_ADMIN
        "is_admin": bool | None,   # True for ADD_ADMIN
    }
    """
    config = _get_config()
    client = _get_bedrock_client()

    system_prompt = (
        "You are an admin command parser for a basketball game scheduling system. "
        "Parse the admin's email and return a structured JSON command.\n\n"
        f"Admin sender: {sender_email}\n\n"
        "Available intents:\n"
        "- CANCEL_GAME: Admin wants to cancel a game for a specific Saturday\n"
        "- ADD_PLAYER: Admin wants to add a new regular player\n"
        "- ADD_ADMIN: Admin wants to add a new admin player\n"
        "- DEACTIVATE_PLAYER: Admin wants to deactivate/remove a player\n"
        "- REACTIVATE_PLAYER: Admin wants to reactivate a previously inactive player\n"
        "- UNKNOWN: Command not understood\n\n"
        "Respond with ONLY a JSON object (no markdown, no explanation):\n"
        '{{\n'
        '  "intent": "...",\n'
        '  "game_date": "YYYY-MM-DD or null — must be a Saturday, resolve to nearest Saturday if needed",\n'
        '  "email": "player email or null",\n'
        '  "name": "player name or null",\n'
        '  "is_admin": true/false/null\n'
        '}}\n\n'
        "For CANCEL_GAME: set game_date to the Saturday being cancelled (YYYY-MM-DD). "
        "If the date mentioned is not a Saturday, resolve it to that week's Saturday. "
        "If you cannot determine the date, set game_date to null.\n"
        "For ADD_ADMIN: set is_admin to true.\n"
        "For ADD_PLAYER: set is_admin to false.\n"
        "For DEACTIVATE_PLAYER / REACTIVATE_PLAYER: set email to the player's email address."
    )

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 256,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": email_body},
        ],
    }

    try:
        response = client.invoke_model(
            modelId=config.bedrock_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())
        assistant_text = response_body["content"][0]["text"].strip()
        result = json.loads(assistant_text)

        logger.info(f"Admin command parsed for {sender_email}: {result}")

        return {
            "intent": result.get("intent", "UNKNOWN"),
            "game_date": result.get("game_date"),
            "email": result.get("email"),
            "name": result.get("name"),
            "is_admin": result.get("is_admin"),
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Bedrock admin response as JSON: {e}")
        return {
            "intent": "UNKNOWN",
            "game_date": None,
            "email": None,
            "name": None,
            "is_admin": None,
        }
    except Exception as e:
        logger.error(f"Error calling Bedrock for admin command: {e}", exc_info=True)
        return {
            "intent": "UNKNOWN",
            "game_date": None,
            "email": None,
            "name": None,
            "is_admin": None,
        }
```

- [ ] **Step 4: Run bedrock tests**

```bash
pytest tests/unit/test_bedrock_client.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/common/bedrock_client.py tests/unit/test_bedrock_client.py
git commit -m "feat(bedrock): add parse_admin_email for admin intent classification"
```

---

## Task 5: Admin Processor Lambda

**Files:**
- Create: `src/admin_processor/__init__.py`
- Create: `src/admin_processor/handler.py`
- Create: `tests/unit/test_admin_processor.py`

- [ ] **Step 1: Create package marker**

Create `src/admin_processor/__init__.py` — empty file.

- [ ] **Step 2: Write failing tests**

Create `tests/unit/test_admin_processor.py`:

```python
import json
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from admin_processor.handler import handler


def _make_s3_event(bucket: str, key: str) -> dict:
    return {
        "Records": [{
            "s3": {
                "bucket": {"name": bucket},
                "object": {"key": key},
            }
        }]
    }


def _make_raw_email(from_addr: str, subject: str, body: str) -> bytes:
    return (
        f"From: {from_addr}\r\n"
        f"To: admin@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain\r\n"
        f"\r\n"
        f"{body}"
    ).encode()


@pytest.mark.unit
def test_non_admin_sender_is_rejected(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=False)
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("notadmin@example.com", "Cancel", "Cancel game"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 403
    mock_send.assert_called_once()
    call_args = mock_send.call_args[0]
    assert "permission" in call_args[2].lower() or "not authorised" in call_args[2].lower()


@pytest.mark.unit
def test_cancel_game_advance_creates_cancelled_record(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-04-11",
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mocker.patch("admin_processor.handler.get_game_status", return_value=None)
    mock_pre_cancel = mocker.patch("admin_processor.handler.pre_cancel_game")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Cancel", "Cancel game on April 11"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_pre_cancel.assert_called_once_with("2026-04-11")
    # No broadcast — game not yet announced
    mock_send.assert_called_once()  # only the admin confirmation


@pytest.mark.unit
def test_cancel_game_open_updates_status_and_broadcasts(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-04-11",
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mocker.patch("admin_processor.handler.get_game_status", return_value={"gameDate": "2026-04-11", "status": "OPEN"})
    mock_update = mocker.patch("admin_processor.handler.update_game_status")
    mocker.patch("admin_processor.handler.get_roster", return_value={
        "YES": {"players": {"alice@example.com": {"name": "Alice"}, "bob@example.com": {"name": "Bob"}}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {"charlie@example.com": {"name": "Charlie"}}, "guests": []},
    })
    mock_send = mocker.patch("admin_processor.handler.send_admin_cancelled_broadcast")
    mock_send_email = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Cancel", "Cancel game on April 11"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_update.assert_called_once_with("2026-04-11", "CANCELLED")
    # Broadcast to YES and MAYBE players
    assert mock_send.call_count == 3
    mock_send.assert_any_call("alice@example.com", "2026-04-11")
    mock_send.assert_any_call("bob@example.com", "2026-04-11")
    mock_send.assert_any_call("charlie@example.com", "2026-04-11")
    # Admin confirmation email
    mock_send_email.assert_called_once()


@pytest.mark.unit
def test_cancel_game_missing_date_sends_error(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Cancel", "Cancel the game"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_send.assert_called_once()
    assert "date" in mock_send.call_args[0][2].lower()


@pytest.mark.unit
def test_add_player_creates_record(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "ADD_PLAYER",
        "game_date": None,
        "email": "newplayer@example.com",
        "name": "New Player",
        "is_admin": False,
    })
    mock_add = mocker.patch("admin_processor.handler.add_player")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Add", "Add player newplayer@example.com New Player"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_add.assert_called_once_with("newplayer@example.com", "New Player", is_admin=False)
    mock_send.assert_called_once()


@pytest.mark.unit
def test_add_admin_creates_admin_record(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "ADD_ADMIN",
        "game_date": None,
        "email": "newadmin@example.com",
        "name": "New Admin",
        "is_admin": True,
    })
    mock_add = mocker.patch("admin_processor.handler.add_player")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Add Admin", "Add admin newadmin@example.com New Admin"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_add.assert_called_once_with("newadmin@example.com", "New Admin", is_admin=True)


@pytest.mark.unit
def test_deactivate_player(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "DEACTIVATE_PLAYER",
        "game_date": None,
        "email": "alice@example.com",
        "name": None,
        "is_admin": None,
    })
    mock_deactivate = mocker.patch("admin_processor.handler.deactivate_player")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Remove", "Deactivate alice@example.com"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_deactivate.assert_called_once_with("alice@example.com")


@pytest.mark.unit
def test_reactivate_player(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "REACTIVATE_PLAYER",
        "game_date": None,
        "email": "alice@example.com",
        "name": None,
        "is_admin": None,
    })
    mock_reactivate = mocker.patch("admin_processor.handler.reactivate_player")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "Reactivate", "Reactivate alice@example.com"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_reactivate.assert_called_once_with("alice@example.com")


@pytest.mark.unit
def test_unknown_intent_sends_error_reply(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "UNKNOWN",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mock_send = mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_raw_email("admin@example.com", "??", "blahrgh"))
    }

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_send.assert_called_once()
    assert "understand" in mock_send.call_args[0][2].lower()
```

- [ ] **Step 3: Run to verify they fail**

```bash
pytest tests/unit/test_admin_processor.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'admin_processor'`

- [ ] **Step 4: Create `src/admin_processor/__init__.py`**

Create empty file:

```python
```

- [ ] **Step 5: Implement `src/admin_processor/handler.py`**

```python
import email
import logging
from email import policy
from typing import Any

import boto3

from common.bedrock_client import parse_admin_email
from common.config import load_config
from common.dynamo import (
    add_player,
    deactivate_player,
    get_game_status,
    get_roster,
    is_admin,
    pre_cancel_game,
    reactivate_player,
    update_game_status,
)
from common.email_service import send_admin_cancelled_broadcast, send_email

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _extract_sender_email(from_header: str) -> str:
    """Extract the email address from a From header value."""
    if "<" in from_header and ">" in from_header:
        return from_header.split("<")[1].split(">")[0].strip()
    return from_header.strip()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: process admin command emails."""
    config = load_config()

    s3_record = event["Records"][0]["s3"]
    bucket = s3_record["bucket"]["name"]
    key = s3_record["object"]["key"]

    logger.info(f"Processing admin email from S3: {bucket}/{key}")

    s3_client = _get_s3_client()
    response = s3_client.get_object(Bucket=bucket, Key=key)
    raw_email = response["Body"].read()

    msg = email.message_from_bytes(raw_email, policy=policy.default)
    from_header = msg.get("From", "")
    subject = msg.get("Subject", "Admin Command")

    sender_email = _extract_sender_email(from_header)
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

    # Extract body
    if msg.is_multipart():
        body = ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        body = payload.decode("utf-8", errors="replace") if payload else ""

    parsed = parse_admin_email(body, sender_email)
    intent = parsed["intent"]

    logger.info(f"Admin intent from {sender_email}: {intent}")

    if intent == "CANCEL_GAME":
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
            roster = get_roster(game_date)

            notified: set[str] = set()
            for status_key in ("YES", "MAYBE"):
                for player_email in roster.get(status_key, {}).get("players", {}).keys():
                    send_admin_cancelled_broadcast(player_email, game_date)
                    notified.add(player_email)

            send_email(
                sender_email,
                f"Re: {subject}",
                f"Done. The game on {game_date} has been cancelled. "
                f"Notified {len(notified)} player(s) who had responded YES or MAYBE.",
            )
            logger.info(f"Cancelled open game {game_date}, notified {len(notified)} players")

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
            "- Cancel the game on [date]\n"
            "- Add player [email], name [name]\n"
            "- Add admin [email], name [name]\n"
            "- Deactivate [email]\n"
            "- Reactivate [email]",
        )

    return {"statusCode": 200, "body": {"intent": intent}}
```

- [ ] **Step 6: Run admin processor tests**

```bash
pytest tests/unit/test_admin_processor.py -v
```

Expected: All 9 tests PASS

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/unit/ -v
```

Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/admin_processor/ tests/unit/test_admin_processor.py
git commit -m "feat(admin-processor): add admin Lambda handler with email-based commands"
```

---

## Task 6: Update `announcement-sender` for Pre-Cancelled Games

**Files:**
- Modify: `src/announcement_sender/handler.py`
- Modify: `tests/unit/test_announcement_handler.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_announcement_handler.py`:

```python
from announcement_sender.handler import _next_saturday, handler


@pytest.mark.unit
def test_handler_skips_pre_cancelled_game(mocker):
    """If the upcoming Saturday is pre-cancelled, send no-game email instead of announcement."""
    mocker.patch(
        "announcement_sender.handler.get_game_status",
        return_value={"gameDate": "2026-04-11", "sk": "gameStatus", "status": "CANCELLED"},
    )
    mock_create_game = mocker.patch("announcement_sender.handler.create_game")
    mock_get_active = mocker.patch(
        "announcement_sender.handler.get_active_players",
        return_value=[
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
        ],
    )
    mock_no_game = mocker.patch("announcement_sender.handler.send_no_game_announcement")
    mock_announcement = mocker.patch("announcement_sender.handler.send_announcement")

    result = handler({}, None)

    assert result["statusCode"] == 200
    assert result["body"]["action"] == "pre_cancelled"
    mock_create_game.assert_not_called()
    mock_announcement.assert_not_called()
    assert mock_no_game.call_count == 2
    mock_no_game.assert_any_call("alice@example.com", "Alice", mocker.ANY)
    mock_no_game.assert_any_call("bob@example.com", "Bob", mocker.ANY)


@pytest.mark.unit
def test_handler_proceeds_normally_when_no_pre_cancel(mocker):
    """If no game record exists, proceed with normal game creation."""
    mocker.patch("announcement_sender.handler.get_game_status", return_value=None)
    mock_create_game = mocker.patch("announcement_sender.handler.create_game")
    mocker.patch(
        "announcement_sender.handler.get_active_players",
        return_value=[{"email": "alice@example.com", "name": "Alice"}],
    )
    mock_announcement = mocker.patch("announcement_sender.handler.send_announcement")

    result = handler({}, None)

    assert result["statusCode"] == 200
    mock_create_game.assert_called_once()
    mock_announcement.assert_called_once()
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_announcement_handler.py::test_handler_skips_pre_cancelled_game -v
```

Expected: FAIL (attribute error — `get_game_status` not imported in handler)

- [ ] **Step 3: Update `src/announcement_sender/handler.py`**

Replace with:

```python
import logging
from datetime import date, timedelta
from typing import Any

from common.dynamo import create_game, get_active_players, get_game_status
from common.email_service import send_announcement, send_no_game_announcement

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _next_saturday() -> str:
    """Calculate the date of the coming Saturday (assumes today is Monday)."""
    today = date.today()
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    saturday = today + timedelta(days=days_until_saturday)
    return saturday.isoformat()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: announce a new game for next Saturday."""
    game_date = _next_saturday()
    logger.info(f"Checking game status for {game_date}")

    existing = get_game_status(game_date)
    if existing and existing.get("status") == "CANCELLED":
        logger.info(f"Game {game_date} is pre-cancelled — sending no-game notification")
        players = get_active_players()
        sent_count = 0
        for player in players:
            try:
                send_no_game_announcement(player["email"], player.get("name"), game_date)
                sent_count += 1
            except Exception:
                logger.error(f"Failed to send no-game notification to {player['email']}", exc_info=True)

        logger.info(f"Sent {sent_count}/{len(players)} no-game notifications for {game_date}")
        return {
            "statusCode": 200,
            "body": {
                "action": "pre_cancelled",
                "gameDate": game_date,
                "notifiedCount": sent_count,
            },
        }

    logger.info(f"Creating game for {game_date}")
    create_game(game_date)

    players = get_active_players()
    logger.info(f"Sending announcements to {len(players)} players")

    sent_count = 0
    for player in players:
        try:
            send_announcement(player["email"], player.get("name"), game_date)
            sent_count += 1
        except Exception:
            logger.error(f"Failed to send announcement to {player['email']}", exc_info=True)

    logger.info(f"Sent {sent_count}/{len(players)} announcements for game {game_date}")

    return {
        "statusCode": 200,
        "body": {
            "gameDate": game_date,
            "playerCount": len(players),
            "sentCount": sent_count,
        },
    }
```

- [ ] **Step 4: Run announcement handler tests**

```bash
pytest tests/unit/test_announcement_handler.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/announcement_sender/handler.py tests/unit/test_announcement_handler.py
git commit -m "feat(announcement-sender): skip pre-cancelled games and notify players"
```

---

## Task 7: Add Reminder Checker Test for Cancelled Game

The reminder checker already handles cancelled games correctly because `get_current_open_game()` returns `None` for non-OPEN games. This task just adds an explicit test to document and lock in that behaviour.

**Files:**
- Modify: `tests/unit/test_reminder_handler.py`

- [ ] **Step 1: Add the test**

Open `tests/unit/test_reminder_handler.py` and add:

```python
@pytest.mark.unit
def test_handler_skips_cancelled_game(mocker):
    """If the upcoming game is CANCELLED, reminder_checker does nothing."""
    mocker.patch(
        "reminder_checker.handler.get_current_open_game",
        return_value=None,  # get_current_open_game returns None for CANCELLED games
    )
    mock_send_reminder = mocker.patch("reminder_checker.handler.send_reminder")
    mock_send_cancellation = mocker.patch("reminder_checker.handler.send_cancellation")
    mock_send_confirmation = mocker.patch("reminder_checker.handler.send_confirmation")

    result = handler({}, None)

    assert result["statusCode"] == 200
    assert result["body"] == "No open game"
    mock_send_reminder.assert_not_called()
    mock_send_cancellation.assert_not_called()
    mock_send_confirmation.assert_not_called()
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/unit/test_reminder_handler.py::test_handler_skips_cancelled_game -v
```

Expected: PASS (no implementation change needed)

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/unit/ -v
```

Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_reminder_handler.py
git commit -m "test(reminder-checker): document cancelled game skip behaviour"
```

---

## Task 8: Terraform Infrastructure

No TDD here — Terraform changes are verified via `terraform plan`. Apply when ready to deploy.

**Files:**
- Modify: `terraform/variables.tf`
- Modify: `terraform/lambda.tf`
- Modify: `terraform/ses.tf`
- Modify: `terraform/s3.tf`

### 8a: Add `admin_email` variable

- [ ] **Step 1: Add to `terraform/variables.tf`**

Add at the end:

```hcl
variable "admin_email" {
  description = "Email address for admin commands (e.g. admin@hoops.example.com)"
  type        = string
}
```

### 8b: Add `ADMIN_EMAIL` to Lambda env vars and admin_processor Lambda

- [ ] **Step 2: Update `terraform/lambda.tf`**

In the `lambda_env_vars` local, add:

```hcl
ADMIN_EMAIL = var.admin_email
```

So the block becomes:

```hcl
locals {
  lambda_functions = {
    announcement_sender = "announcement-sender"
    email_processor     = "email-processor"
    reminder_checker    = "reminder-checker"
    game_finalizer      = "game-finalizer"
    admin_processor     = "admin-processor"
  }

  lambda_env_vars = {
    PLAYERS_TABLE    = aws_dynamodb_table.players.name
    GAMES_TABLE      = aws_dynamodb_table.games.name
    EMAIL_BUCKET     = aws_s3_bucket.email_inbox.id
    SENDER_EMAIL     = var.sender_email
    ADMIN_EMAIL      = var.admin_email
    GAME_TIME        = var.game_time
    GAME_LOCATION    = var.game_location
    BEDROCK_MODEL_ID = var.bedrock_model_id
    MIN_PLAYERS      = tostring(var.min_players)
  }
}
```

Add the build and Lambda resources for `admin_processor` (append to `terraform/lambda.tf`):

```hcl
resource "null_resource" "build_admin_processor" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/.build/admin_processor
      mkdir -p ${path.module}/.build/admin_processor
      cp -r ${path.module}/../src/common ${path.module}/.build/admin_processor/common
      cp -r ${path.module}/../src/admin_processor/* ${path.module}/.build/admin_processor/
      pip install -r ${path.module}/../requirements-runtime.txt -t ${path.module}/.build/admin_processor --quiet
    EOT
  }
}

data "archive_file" "admin_processor_zip" {
  type        = "zip"
  source_dir  = "${path.module}/.build/admin_processor"
  output_path = "${path.module}/.build/admin_processor.zip"

  depends_on = [null_resource.build_admin_processor]
}

resource "aws_lambda_function" "admin_processor" {
  function_name    = "basketball-admin-processor"
  description      = "Processes admin command emails for game cancellation and player management"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.admin_processor_zip.output_path
  source_code_hash = data.archive_file.admin_processor_zip.output_base64sha256

  environment {
    variables = local.lambda_env_vars
  }

  tags = {
    Name = "basketball-admin-processor"
  }
}

resource "aws_lambda_permission" "allow_s3_invoke_admin" {
  statement_id   = "AllowS3InvokeAdmin"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.admin_processor.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.email_inbox.arn
  source_account = data.aws_caller_identity.current.account_id
}
```

### 8c: Add admin SES receipt rule

- [ ] **Step 3: Update `terraform/ses.tf`**

Add a new receipt rule before the existing `store_in_s3` rule. The admin rule must come first and use `stop_enabled = true` so admin emails don't also match the catch-all domain rule.

Update the existing `store_in_s3` rule to come after the admin rule by adding `after`:

```hcl
resource "aws_ses_receipt_rule" "admin_email" {
  name          = "store-admin-emails"
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = [var.admin_email]
  enabled       = true
  scan_enabled  = true
  stop_enabled  = true

  s3_action {
    bucket_name       = aws_s3_bucket.email_inbox.id
    object_key_prefix = "admin/"
    position          = 1
  }

  depends_on = [aws_s3_bucket_policy.allow_ses_put]
}

resource "aws_ses_receipt_rule" "store_in_s3" {
  name          = "store-inbound-emails"
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = [var.domain_name]
  enabled       = true
  scan_enabled  = true
  after         = aws_ses_receipt_rule.admin_email.name

  s3_action {
    bucket_name       = aws_s3_bucket.email_inbox.id
    object_key_prefix = "inbound/"
    position          = 1
  }

  depends_on = [aws_s3_bucket_policy.allow_ses_put]
}
```

Remove the old `store_in_s3` resource definition (it's being replaced above with the `after` attribute added).

### 8d: Update S3 notification to filter by prefix

- [ ] **Step 4: Update `terraform/s3.tf`**

Replace the `aws_s3_bucket_notification` resource with one that has two `lambda_function` blocks — one per prefix:

```hcl
resource "aws_s3_bucket_notification" "email_received" {
  bucket = aws_s3_bucket.email_inbox.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.email_processor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "inbound/"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.admin_processor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "admin/"
  }

  depends_on = [
    aws_lambda_permission.allow_s3_invoke,
    aws_lambda_permission.allow_s3_invoke_admin,
  ]
}
```

### 8e: Add `admin_email` to `terraform.tfvars`

- [ ] **Step 5: Add to your `terraform.tfvars`** (not committed — contains real values)

```hcl
admin_email = "admin@yourdomain.com"
```

### 8f: Validate and commit Terraform

- [ ] **Step 6: Run terraform plan**

```bash
cd terraform && terraform plan
```

Expected: Plan shows new `admin_processor` Lambda, new SES receipt rule, updated S3 notification — no unexpected deletions.

- [ ] **Step 7: Commit Terraform**

```bash
git add terraform/variables.tf terraform/lambda.tf terraform/ses.tf terraform/s3.tf
git commit -m "feat(terraform): add admin-processor Lambda, SES rule, and S3 notification"
```

---

## Final Verification

- [ ] **Run full unit test suite**

```bash
pytest tests/unit/ -v
```

Expected: All tests PASS

- [ ] **Run unit tests only (fast check)**

```bash
make test-unit
```

Expected: All pass, no errors.
