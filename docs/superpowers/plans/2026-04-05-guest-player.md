# Guest Player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make guests first-class per-game entries stored in both the Players table and as a top-level `guests` array on `playerStatus#*` Games table items, with full lifecycle: declare → confirm-after-cancel → cleanup.

**Architecture:** Guests are created in the Players table (PK=email or sponsorEmail, SK=guest#active or guest#active#name) immediately when declared via `BRING_GUESTS`/`UPDATE_GUESTS`. Each `playerStatus#YES/NO/MAYBE` item gains a top-level `guests` list of `{pk, sk, name, sponsorEmail, sponsorName}` objects. When a player declines, their guests move to the NO `guests` array; a follow-up email lets the sponsor confirm which guests still attend (`GUEST_CONFIRM`). `game_finalizer` cleans up all guest Players entries after the game is marked PLAYED.

**Tech Stack:** Python 3.12, boto3, moto (unit tests), LocalStack (integration tests), AWS Bedrock Claude Haiku (NLU)

---

## File Map

| File | Change type | What changes |
|---|---|---|
| `src/common/dynamo.py` | Modify | Schema init, `get_roster` return type, `update_player_response` name param, + 6 new guest functions |
| `src/common/email_service.py` | Modify | `send_confirmation` for new roster structure; add `send_guest_followup` |
| `src/common/bedrock_client.py` | Modify | New intents `GUEST_CONFIRM`/`GUEST_DECLINE`, response schema adds `guests` list + `confirmed_guest_names` |
| `src/email_processor/handler.py` | Modify | New intent handlers, updated roster helpers, guest Players table writes |
| `src/reminder_checker/handler.py` | Modify | `_count_confirmed` uses new roster structure |
| `src/game_finalizer/handler.py` | Modify | Guest cleanup after marking PLAYED |
| `tests/unit/conftest.py` | Modify | Players table gets `active` RANGE key |
| `tests/unit/test_dynamo.py` | Modify | Update `_create_tables`, existing roster tests, add new dynamo function tests |
| `tests/unit/test_email_processor_handler.py` | Modify | Update for new roster structure, add guest intent tests |
| `tests/unit/test_reminder_handler.py` | Modify | Update `_count_confirmed` test for new roster shape |
| `tests/unit/test_game_finalizer.py` | Modify | Add guest cleanup tests |
| `tests/integration/conftest.py` | Modify | `seed_game` adds `guests: []` to playerStatus items |
| `tests/integration/test_game_finalizer_flow.py` | Modify | Add guest cleanup integration test |

---

## Task 1: Add `active` RANGE key to Players table in test fixtures

The Players table currently has only `PK=email` in unit tests. Guest entries need `PK=email, SK=active` so both permanent players (`active="true"`) and guests (`active="guest#active"` or `active="guest#active#<name>"`) can coexist and be point-deleted.

**Files:**
- Modify: `tests/unit/conftest.py`
- Modify: `tests/unit/test_dynamo.py`

- [ ] **Step 1: Update `dynamodb_tables` fixture in unit conftest**

In `tests/unit/conftest.py`, replace the Players table creation block:

```python
# Create Players table
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
```

- [ ] **Step 2: Update `_create_tables` helper in `test_dynamo.py`**

Replace the Players table creation in `_create_tables()`:

```python
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
```

- [ ] **Step 3: Run existing unit tests to verify nothing is broken**

```bash
make test-unit
```

Expected: all existing tests pass (players already have `active` attribute in their put_item calls, so the range key is satisfied).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/conftest.py tests/unit/test_dynamo.py
git commit -m "feat: add active range key to Players table in unit test fixtures"
```

---

## Task 2: Add `guests` list to `playerStatus#*` items in `create_game`

Each `playerStatus#YES/NO/MAYBE` item needs a top-level `guests` list initialised to `[]`.

**Files:**
- Modify: `src/common/dynamo.py`
- Modify: `tests/unit/test_dynamo.py`

- [ ] **Step 1: Write failing test**

In `tests/unit/test_dynamo.py`, update `test_create_game` to assert the guests list:

```python
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

    game_status_item = next(i for i in items if i["sk"] == "gameStatus")
    assert game_status_item["status"] == "OPEN"
    assert "createdAt" in game_status_item

    for sk in ("playerStatus#YES", "playerStatus#NO", "playerStatus#MAYBE"):
        item = next(i for i in items if i["sk"] == sk)
        assert item["players"] == {}
        assert item["guests"] == []  # NEW assertion
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/unit/test_dynamo.py::test_create_game -v
```

Expected: FAIL — `assert item["guests"] == []` KeyError or assertion error.

- [ ] **Step 3: Update `create_game` in `src/common/dynamo.py`**

Add `"guests": {"L": []}` to each playerStatus PutRequest item:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_dynamo.py::test_create_game -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/common/dynamo.py tests/unit/test_dynamo.py
git commit -m "feat: initialise guests list on playerStatus items in create_game"
```

---

## Task 3: Update `get_roster` to return new structure and update all callers

`get_roster` currently returns `{"YES": {email: {"guests": [...strings...]}}, ...}`. The new structure is `{"YES": {"players": {email: {"name": str}}, "guests": [guest_obj]}, ...}`. This is a breaking change — all callers must be updated in the same commit.

**Files:**
- Modify: `src/common/dynamo.py`
- Modify: `src/reminder_checker/handler.py`
- Modify: `src/email_processor/handler.py`
- Modify: `src/common/email_service.py`
- Modify: `tests/unit/test_dynamo.py`
- Modify: `tests/unit/test_reminder_handler.py`

- [ ] **Step 1: Write failing test for new `get_roster` return structure**

In `tests/unit/test_dynamo.py`, add a new test (keep the old one for now — it will be updated in step 3):

```python
@pytest.mark.unit
@mock_aws
def test_get_roster_new_structure(sample_game_date):
    """get_roster returns players map and guests list per status."""
    _reset_dynamo_caches()
    _create_tables()

    create_game(sample_game_date)
    update_player_response(sample_game_date, "alice@example.com", "YES", name="Alice")
    update_player_response(sample_game_date, "bob@example.com", "NO", name="Bob")

    roster = get_roster(sample_game_date)

    assert "players" in roster["YES"]
    assert "guests" in roster["YES"]
    assert "alice@example.com" in roster["YES"]["players"]
    assert roster["YES"]["players"]["alice@example.com"]["name"] == "Alice"
    assert roster["YES"]["guests"] == []
    assert "bob@example.com" in roster["NO"]["players"]
    assert roster["NO"]["guests"] == []
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/unit/test_dynamo.py::test_get_roster_new_structure -v
```

Expected: FAIL.

- [ ] **Step 3: Update `get_roster` in `src/common/dynamo.py`**

```python
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
```

- [ ] **Step 4: Update `update_player_response` to accept and store `name`**

Add `name: str | None = None` parameter and store `{"name": name_val}` in the player map value instead of `{"guests": [...]}`:

```python
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
```

- [ ] **Step 5: Update `_count_confirmed` in `src/reminder_checker/handler.py`**

```python
def _count_confirmed(roster: dict[str, Any]) -> int:
    """Count confirmed players including their guests."""
    yes = roster.get("YES", {})
    return len(yes.get("players", {})) + len(yes.get("guests", []))
```

- [ ] **Step 6: Update `_find_player_status` in `src/email_processor/handler.py`**

```python
def _find_player_status(sender_email: str, roster: dict[str, Any]) -> str | None:
    """Find the player's current status in the roster."""
    for status, data in roster.items():
        if sender_email in data.get("players", {}):
            return status
    return None
```

- [ ] **Step 7: Update `_format_roster_summary` in `src/email_processor/handler.py`**

```python
def _format_roster_summary(roster: dict[str, Any]) -> str:
    """Format current roster into a readable summary for reply emails."""
    sections = []

    for status, label in [("YES", "Playing"), ("NO", "Not Playing"), ("MAYBE", "Maybe")]:
        data = roster.get(status, {})
        players = data.get("players", {})
        guests = data.get("guests", [])
        if players or guests:
            lines = []
            for player_email, pdata in players.items():
                name = pdata.get("name") or player_email
                lines.append(f"  - {name} ({player_email})")
            for guest in guests:
                lines.append(f"  + Guest: {guest['name']} (via {guest['sponsorName']})")
            sections.append(f"{label} ({len(players)} players, {len(guests)} guests):\n" + "\n".join(lines))

    if not sections:
        return "\n\n---\nNo responses yet."

    return "\n\n---\nCurrent Responses:\n\n" + "\n\n".join(sections)
```

- [ ] **Step 8: Update `send_confirmation` in `src/common/email_service.py`**

```python
def send_confirmation(
    player_email: str,
    game_date: str,
    roster: dict[str, Any],
) -> None:
    """Send final confirmation with roster to confirmed players."""
    config = _get_config()

    subject = f"Confirmed: Basketball Game - {game_date}"

    yes_data = roster.get("YES", {})
    lines: list[str] = []
    for email, data in yes_data.get("players", {}).items():
        name = data.get("name") or email
        lines.append(f"  - {name} ({email})")
    for guest in yes_data.get("guests", []):
        lines.append(f"    + Guest: {guest['name']} (via {guest['sponsorName']})")

    roster_text = "\n".join(lines) if lines else "  (none)"

    body = (
        f"Hi,\n\n"
        f"The basketball game is ON for {game_date}!\n\n"
        f"Time: {config.game_time}\n"
        f"Location: {config.game_location}\n\n"
        f"Confirmed players:\n{roster_text}\n\n"
        f"See you there!\n"
    )

    send_email(player_email, subject, body)
```

- [ ] **Step 9: Update existing tests that use old roster structure**

In `tests/unit/test_dynamo.py`, replace `test_get_roster` and `test_update_player_response_with_guests`:

```python
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
```

Remove `test_update_player_response_with_guests` — it tested the old nested guest strings which no longer apply.

Also delete the new `test_get_roster_new_structure` test added in Step 1 (it's now covered by the updated `test_get_roster`).

- [ ] **Step 10: Update `test_reminder_handler.py` for new roster shape**

Find all tests that build a `roster` dict and update them. The `_count_confirmed` helper is the main thing to fix. In `tests/unit/test_reminder_handler.py`, any dict like `{"YES": {"alice@example.com": {"guests": []}}}` must become `{"YES": {"players": {"alice@example.com": {"name": "Alice"}}, "guests": []}}`. Make this change wherever it appears in that file.

- [ ] **Step 11: Run all unit tests**

```bash
make test-unit
```

Expected: all tests pass.

- [ ] **Step 12: Commit**

```bash
git add src/common/dynamo.py src/common/email_service.py src/email_processor/handler.py src/reminder_checker/handler.py tests/unit/test_dynamo.py tests/unit/test_reminder_handler.py
git commit -m "feat: update get_roster to return players map + guests list per status"
```

---

## Task 4: Add `get_player_name` to `dynamo.py`

`email_processor` needs to look up a player's name before writing to the Games table. This is a simple point read on the Players table.

**Files:**
- Modify: `src/common/dynamo.py`
- Modify: `tests/unit/test_dynamo.py`

- [ ] **Step 1: Write failing test**

In `tests/unit/test_dynamo.py`:

```python
@pytest.mark.unit
@mock_aws
def test_get_player_name_found(sample_game_date):
    """Returns name for a known active player."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()
    table = dynamodb.Table("test-players")
    table.put_item(Item={"email": "alice@example.com", "active": "true", "name": "Alice"})

    from common.dynamo import get_player_name
    result = get_player_name("alice@example.com")
    assert result == "Alice"


@pytest.mark.unit
@mock_aws
def test_get_player_name_not_found():
    """Returns None for unknown player."""
    _reset_dynamo_caches()
    _create_tables()

    from common.dynamo import get_player_name
    result = get_player_name("nobody@example.com")
    assert result is None
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_dynamo.py::test_get_player_name_found tests/unit/test_dynamo.py::test_get_player_name_not_found -v
```

Expected: FAIL — `ImportError: cannot import name 'get_player_name'`.

- [ ] **Step 3: Add `get_player_name` to `src/common/dynamo.py`**

```python
def get_player_name(email: str) -> str | None:
    """Get the name of an active player from the Players table."""
    config = _get_config()
    table = _get_resource().Table(config.players_table)

    response = table.get_item(Key={"email": email, "active": "true"})
    item = response.get("Item")
    if item:
        return item.get("name")
    return None
```

Also add `get_player_name` to the imports in `tests/unit/test_dynamo.py` at the top:

```python
from common.dynamo import (
    _next_saturday,
    create_game,
    get_active_players,
    get_current_open_game,
    get_game_status,
    get_pending_players,
    get_player_name,
    get_roster,
    update_game_status,
    update_player_response,
)
```

Remove the inline `from common.dynamo import get_player_name` lines inside the tests.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_dynamo.py::test_get_player_name_found tests/unit/test_dynamo.py::test_get_player_name_not_found -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/common/dynamo.py tests/unit/test_dynamo.py
git commit -m "feat: add get_player_name to dynamo"
```

---

## Task 5: Add guest CRUD functions to `dynamo.py`

Add six new functions that manage guest lifecycle: create/delete Players entries, and append/remove/move guest objects on the `guests` list of `playerStatus#*` items.

**Files:**
- Modify: `src/common/dynamo.py`
- Modify: `tests/unit/test_dynamo.py`

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_dynamo.py`:

```python
@pytest.mark.unit
@mock_aws
def test_create_guest_entry_with_contact_email(sample_game_date):
    """Guest with contact email uses contactEmail as PK, sk=guest#active."""
    _reset_dynamo_caches()
    dynamodb = _create_tables()

    from common.dynamo import create_guest_entry
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

    from common.dynamo import create_guest_entry
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

    from common.dynamo import create_guest_entry, delete_guest_entries
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

    from common.dynamo import add_guests_to_game_status
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

    from common.dynamo import add_guests_to_game_status, remove_sponsor_guests_from_status
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
    # Bob (different sponsor) should remain
    assert len(roster["YES"]["guests"]) == 1
    assert roster["YES"]["guests"][0]["name"] == "Bob"


@pytest.mark.unit
@mock_aws
def test_move_confirmed_guests(sample_game_date):
    """Moves named guests from NO guests array to YES guests array."""
    _reset_dynamo_caches()
    _create_tables()
    create_game(sample_game_date)

    from common.dynamo import add_guests_to_game_status, move_confirmed_guests
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_dynamo.py::test_create_guest_entry_with_contact_email tests/unit/test_dynamo.py::test_create_guest_entry_without_contact_email tests/unit/test_dynamo.py::test_delete_guest_entries tests/unit/test_dynamo.py::test_add_guests_to_game_status tests/unit/test_dynamo.py::test_remove_sponsor_guests_from_status tests/unit/test_dynamo.py::test_move_confirmed_guests -v
```

Expected: all FAIL.

- [ ] **Step 3: Add the six functions to `src/common/dynamo.py`**

```python
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

    guest_list = [
        {"M": {
            "pk": {"S": g["pk"]},
            "sk": {"S": g["sk"]},
            "name": {"S": g["name"]},
            "sponsorEmail": {"S": g["sponsorEmail"]},
            "sponsorName": {"S": g["sponsorName"]},
        }}
        for g in guests
    ]

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

    remaining_ddb = [
        {"M": {
            "pk": {"S": g["pk"]},
            "sk": {"S": g["sk"]},
            "name": {"S": g["name"]},
            "sponsorEmail": {"S": g["sponsorEmail"]},
            "sponsorName": {"S": g["sponsorName"]},
        }}
        for g in remaining
    ]

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

    def _to_ddb(g: dict) -> dict:
        return {"M": {
            "pk": {"S": g["pk"]},
            "sk": {"S": g["sk"]},
            "name": {"S": g["name"]},
            "sponsorEmail": {"S": g["sponsorEmail"]},
            "sponsorName": {"S": g["sponsorName"]},
        }}

    remaining_ddb = [_to_ddb(g) for g in remaining_no]
    to_move_ddb = [_to_ddb(g) for g in to_move]

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
```

- [ ] **Step 4: Add the new functions to the import in `tests/unit/test_dynamo.py`**

```python
from common.dynamo import (
    _next_saturday,
    add_guests_to_game_status,
    create_game,
    create_guest_entry,
    delete_guest_entries,
    get_active_players,
    get_current_open_game,
    get_game_status,
    get_pending_players,
    get_player_name,
    get_roster,
    move_confirmed_guests,
    remove_sponsor_guests_from_status,
    update_game_status,
    update_player_response,
)
```

Remove the inline `from common.dynamo import ...` inside individual tests.

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_dynamo.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/common/dynamo.py tests/unit/test_dynamo.py
git commit -m "feat: add guest CRUD functions to dynamo (create, delete, add, remove, move)"
```

---

## Task 6: Add `send_guest_followup` to `email_service.py`

When a player with guests declines, the system sends a follow-up asking about their guests.

**Files:**
- Modify: `src/common/email_service.py`
- Modify: `tests/unit/test_email_service.py`

- [ ] **Step 1: Write failing test**

In `tests/unit/test_email_service.py`, add:

```python
@pytest.mark.unit
@mock_aws
def test_send_guest_followup(ses_setup):
    """send_guest_followup sends email to sponsor listing their guests."""
    from common.email_service import send_guest_followup

    send_guest_followup(
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        guest_names=["John", "Jane"],
        game_date="2026-04-05",
    )

    sent = ses_setup.list_sent_messages() if hasattr(ses_setup, "list_sent_messages") else None
    # Verify via SES mock — moto records sent emails
    import boto3
    from moto import mock_aws
    # The send_email call should not raise; if we reach here the call succeeded
```

Note: moto doesn't provide an easy way to inspect email bodies in older versions, so the test verifies the call succeeds without exception and the subject/to are correct by checking the SES send count.

Actually use this pattern (consistent with existing `test_email_service.py`):

```python
@pytest.mark.unit
@mock_aws
def test_send_guest_followup():
    """send_guest_followup sends email listing guests to the sponsor."""
    import boto3
    from moto import mock_aws
    import os

    os.environ.setdefault("PLAYERS_TABLE", "test-players")
    os.environ.setdefault("GAMES_TABLE", "test-games")
    os.environ.setdefault("EMAIL_BUCKET", "test-bucket")
    os.environ.setdefault("SENDER_EMAIL", "scheduler@example.com")

    ses = boto3.client("ses", region_name="eu-west-1")
    ses.verify_email_identity(EmailAddress="scheduler@example.com")

    import common.email_service as email_mod
    email_mod._config = None
    email_mod._ses_client = None

    from common.email_service import send_guest_followup
    # Should not raise
    send_guest_followup(
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        guest_names=["John", "Jane"],
        game_date="2026-04-05",
    )
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/unit/test_email_service.py::test_send_guest_followup -v
```

Expected: FAIL — `ImportError`.

- [ ] **Step 3: Add `send_guest_followup` to `src/common/email_service.py`**

```python
def send_guest_followup(
    sponsor_email: str,
    sponsor_name: str | None,
    guest_names: list[str],
    game_date: str,
) -> None:
    """Ask the sponsor whether their guests are still attending after they declined."""
    greeting = f"Hi {sponsor_name}" if sponsor_name else "Hi"
    guest_list = ", ".join(guest_names)

    subject = f"Your guests for the basketball game on {game_date}"
    body = (
        f"{greeting},\n\n"
        f"We noticed you won't be able to make it to the basketball game on {game_date}. "
        f"You had listed the following guest(s): {guest_list}.\n\n"
        f"Are any of them still planning to attend?\n\n"
        f"Please reply with the names of guests who are still coming, and optionally "
        f"a contact email for each (e.g. 'John - john@example.com, Jane').\n\n"
        f"If no reply is received before Friday's cutoff, we'll assume they won't attend.\n"
    )

    send_email(sponsor_email, subject, body)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_email_service.py::test_send_guest_followup -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/common/email_service.py tests/unit/test_email_service.py
git commit -m "feat: add send_guest_followup email template"
```

---

## Task 7: Update `bedrock_client.py` with new intents and response schema

Add `GUEST_CONFIRM` and `GUEST_DECLINE` intents. Change the response schema: `guest_names: list[str]` becomes `guests: list[{name, contact_email}]`; add `confirmed_guest_names: list[str]` for `GUEST_CONFIRM`.

**Files:**
- Modify: `src/common/bedrock_client.py`
- Modify: `tests/unit/test_bedrock_client.py`

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_bedrock_client.py`, add tests for the new response shape:

```python
@pytest.mark.unit
def test_parse_player_email_bring_guests_new_schema():
    """BRING_GUESTS returns guests as list of {name, contact_email} objects."""
    from unittest.mock import MagicMock, patch
    import json

    mock_response = {
        "intent": "BRING_GUESTS",
        "guests": [
            {"name": "John", "contact_email": "john@example.com"},
            {"name": "Jane", "contact_email": None},
        ],
        "confirmed_guest_names": [],
        "query_target": None,
        "reply_draft": "Got it!",
    }

    with patch("common.bedrock_client._get_bedrock_client") as mock_client_fn, \
         patch("common.bedrock_client._get_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(bedrock_model_id="anthropic.claude-3-haiku-20240307-v1:0")
        mock_bedrock = MagicMock()
        mock_client_fn.return_value = mock_bedrock
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(read=lambda: json.dumps({"content": [{"text": json.dumps(mock_response)}]}).encode())
        }

        from common.bedrock_client import parse_player_email
        result = parse_player_email("I'm in, bringing John (john@example.com) and Jane", "alice@example.com", {})

    assert result["intent"] == "BRING_GUESTS"
    assert len(result["guests"]) == 2
    assert result["guests"][0] == {"name": "John", "contact_email": "john@example.com"}
    assert result["guests"][1] == {"name": "Jane", "contact_email": None}


@pytest.mark.unit
def test_parse_player_email_guest_confirm():
    """GUEST_CONFIRM returns confirmed_guest_names."""
    from unittest.mock import MagicMock, patch
    import json

    mock_response = {
        "intent": "GUEST_CONFIRM",
        "guests": [],
        "confirmed_guest_names": ["John"],
        "query_target": None,
        "reply_draft": "Got it, John is still coming!",
    }

    with patch("common.bedrock_client._get_bedrock_client") as mock_client_fn, \
         patch("common.bedrock_client._get_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(bedrock_model_id="anthropic.claude-3-haiku-20240307-v1:0")
        mock_bedrock = MagicMock()
        mock_client_fn.return_value = mock_bedrock
        mock_bedrock.invoke_model.return_value = {
            "body": MagicMock(read=lambda: json.dumps({"content": [{"text": json.dumps(mock_response)}]}).encode())
        }

        from common.bedrock_client import parse_player_email
        result = parse_player_email("John is still coming", "alice@example.com", {})

    assert result["intent"] == "GUEST_CONFIRM"
    assert result["confirmed_guest_names"] == ["John"]
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_bedrock_client.py::test_parse_player_email_bring_guests_new_schema tests/unit/test_bedrock_client.py::test_parse_player_email_guest_confirm -v
```

Expected: FAIL.

- [ ] **Step 3: Update system prompt and response parsing in `src/common/bedrock_client.py`**

Update the `system_prompt` inside `parse_player_email` — replace the intents section and JSON schema:

```python
system_prompt = (
    "You are a basketball game scheduler assistant. Your job is to interpret "
    "player email replies and determine their intent.\n\n"
    "The sender's email is: {sender_email}\n\n"
    "Current roster:\n{roster_context}\n\n"
    "Available intents:\n"
    "- JOIN: Player wants to play (e.g., 'I'm in', 'Yes', 'Count me in')\n"
    "- DECLINE: Player cannot play (e.g., 'Can't make it', 'No', 'Out')\n"
    "- MAYBE: Player is uncertain (e.g., 'Maybe', 'Not sure yet')\n"
    "- BRING_GUESTS: Player is joining AND bringing guests "
    "(e.g., 'I'm in, bringing John and Jane')\n"
    "- UPDATE_GUESTS: Player already YES but wants to change their guest list\n"
    "- QUERY_ROSTER: Player wants to know the full roster/status\n"
    "- QUERY_PLAYER: Player asks about a specific person\n"
    "- GUEST_CONFIRM: Player previously declined but is confirming some/all of their "
    "guests are still attending (e.g., 'John is still coming')\n"
    "- GUEST_DECLINE: Player previously declined and their guests are also not coming\n\n"
    "Respond with ONLY a JSON object (no markdown, no explanation):\n"
    '{{\n'
    '  "intent": "...",\n'
    '  "guests": [{{"name": "...", "contact_email": null}}],\n'
    '  "confirmed_guest_names": [],\n'
    '  "query_target": null,\n'
    '  "reply_draft": "A friendly reply to send back to the player"\n'
    '}}\n\n'
    "For BRING_GUESTS or UPDATE_GUESTS: populate 'guests' with each guest's name and "
    "their contact email if provided (null otherwise).\n"
    "For GUEST_CONFIRM: populate 'confirmed_guest_names' with the names of guests "
    "the sponsor confirmed are still attending.\n"
    "For QUERY_PLAYER, set query_target to the email or name being asked about.\n"
    "The reply_draft should be a brief, friendly response confirming the action taken."
).format(sender_email=sender_email, roster_context=roster_context)
```

Update the response parsing section:

```python
parsed: dict[str, Any] = {
    "intent": result.get("intent", "MAYBE"),
    "guests": result.get("guests", []),
    "confirmed_guest_names": result.get("confirmed_guest_names", []),
    "query_target": result.get("query_target"),
    "reply_draft": result.get(
        "reply_draft", "Thanks for your reply! We've noted your response."
    ),
}
```

Also update the error return dicts to include the new fields:

```python
return {
    "intent": "MAYBE",
    "guests": [],
    "confirmed_guest_names": [],
    "query_target": None,
    "reply_draft": (
        "Thanks for your reply! I had a little trouble understanding "
        "your message. I've marked you as 'maybe' for now. "
        "Please reply again with a clearer response if needed."
    ),
}
```

(Apply the same `guests`/`confirmed_guest_names` fields to both except blocks.)

- [ ] **Step 4: Update existing `test_bedrock_client.py` tests that reference `guest_names`**

Search for any test referencing `result["guest_names"]` or `result["guest_count"]` and update them to use `result["guests"]` instead. A test like:
```python
assert result["guest_names"] == []
```
becomes:
```python
assert result["guests"] == []
```

- [ ] **Step 5: Run all bedrock tests**

```bash
pytest tests/unit/test_bedrock_client.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/common/bedrock_client.py tests/unit/test_bedrock_client.py
git commit -m "feat: update bedrock intents for guest confirm/decline and new guests response schema"
```

---

## Task 8: Update `email_processor` — `BRING_GUESTS` and `UPDATE_GUESTS`

Wire up the new dynamo guest functions into the existing BRING_GUESTS and UPDATE_GUESTS intent handlers.

**Files:**
- Modify: `src/email_processor/handler.py`
- Modify: `tests/unit/test_email_processor_handler.py`

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_email_processor_handler.py`, add (these mock out the dynamo calls and verify they're called correctly):

```python
@pytest.mark.unit
def test_bring_guests_creates_player_entries():
    """BRING_GUESTS creates guest Players entries and adds to YES guests array."""
    from unittest.mock import MagicMock, patch, call
    import json

    bedrock_result = {
        "intent": "BRING_GUESTS",
        "guests": [
            {"name": "John", "contact_email": "john@example.com"},
            {"name": "Jane", "contact_email": None},
        ],
        "confirmed_guest_names": [],
        "query_target": None,
        "reply_draft": "Got it!",
    }

    raw_email = _make_raw_email("alice@example.com", "Re: Game", "I'm in, bringing John and Jane")

    with patch("email_processor.handler._get_s3_client") as mock_s3_fn, \
         patch("email_processor.handler.get_current_open_game") as mock_game, \
         patch("email_processor.handler.get_roster") as mock_roster, \
         patch("email_processor.handler.parse_player_email") as mock_parse, \
         patch("email_processor.handler.get_player_name") as mock_name, \
         patch("email_processor.handler.update_player_response") as mock_update, \
         patch("email_processor.handler.create_guest_entry") as mock_create_guest, \
         patch("email_processor.handler.add_guests_to_game_status") as mock_add_guests, \
         patch("email_processor.handler.send_email") as mock_send:

        mock_s3_fn.return_value.get_object.return_value = {"Body": MagicMock(read=lambda: raw_email)}
        mock_game.return_value = {"gameDate": "2026-04-05"}
        mock_roster.return_value = {"YES": {"players": {}, "guests": []}, "NO": {"players": {}, "guests": []}, "MAYBE": {"players": {}, "guests": []}}
        mock_parse.return_value = bedrock_result
        mock_name.return_value = "Alice"
        mock_create_guest.side_effect = [
            {"pk": "john@example.com", "sk": "guest#active", "name": "John", "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
            {"pk": "alice@example.com", "sk": "guest#active#Jane", "name": "Jane", "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
        ]

        from email_processor.handler import handler
        result = handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}]}, None)

    assert result["statusCode"] == 200
    assert mock_create_guest.call_count == 2
    mock_create_guest.assert_any_call(
        "2026-04-05", "John", "alice@example.com", "Alice", "john@example.com"
    )
    mock_create_guest.assert_any_call(
        "2026-04-05", "Jane", "alice@example.com", "Alice", None
    )
    mock_add_guests.assert_called_once()
    call_args = mock_add_guests.call_args
    assert call_args[0][1] == "YES"
    assert len(call_args[0][2]) == 2
```

Add a helper at the top of the test file if not already present:

```python
import email as email_lib
from email.mime.text import MIMEText


def _make_raw_email(from_addr: str, subject: str, body: str) -> bytes:
    msg = MIMEText(body)
    msg["From"] = from_addr
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = subject
    return msg.as_bytes()
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/unit/test_email_processor_handler.py::test_bring_guests_creates_player_entries -v
```

Expected: FAIL — import errors or assertion errors.

- [ ] **Step 3: Update `email_processor/handler.py` imports and BRING_GUESTS handler**

Add new imports at the top of the file:

```python
from common.dynamo import (
    add_guests_to_game_status,
    create_guest_entry,
    delete_guest_entries,
    get_current_open_game,
    get_player_name,
    get_roster,
    move_confirmed_guests,
    remove_sponsor_guests_from_status,
    update_player_response,
)
from common.email_service import send_email, send_guest_followup
```

Replace the `BRING_GUESTS` block in `handler()`:

```python
elif intent == "BRING_GUESTS":
    player_name = get_player_name(sender_email)
    update_player_response(game_date, sender_email, "YES", name=player_name, old_status=old_status)
    guest_objects = [
        create_guest_entry(
            game_date,
            g["name"],
            sender_email,
            player_name or sender_email,
            g.get("contact_email"),
        )
        for g in parsed.get("guests", [])
    ]
    if guest_objects:
        add_guests_to_game_status(game_date, "YES", guest_objects)
```

Replace the `UPDATE_GUESTS` block:

```python
elif intent == "UPDATE_GUESTS":
    player_name = get_player_name(sender_email)
    old_guest_objects = remove_sponsor_guests_from_status(game_date, "YES", sender_email)
    if old_guest_objects:
        delete_guest_entries(old_guest_objects)
    new_guest_objects = [
        create_guest_entry(
            game_date,
            g["name"],
            sender_email,
            player_name or sender_email,
            g.get("contact_email"),
        )
        for g in parsed.get("guests", [])
    ]
    if new_guest_objects:
        add_guests_to_game_status(game_date, "YES", new_guest_objects)
```

Also update `JOIN`, `DECLINE`, and `MAYBE` blocks to pass `name`:

```python
if intent == "JOIN":
    player_name = get_player_name(sender_email)
    update_player_response(game_date, sender_email, "YES", name=player_name, old_status=old_status)
elif intent == "DECLINE":
    player_name = get_player_name(sender_email)
    update_player_response(game_date, sender_email, "NO", name=player_name, old_status=old_status)
elif intent == "MAYBE":
    player_name = get_player_name(sender_email)
    update_player_response(game_date, sender_email, "MAYBE", name=player_name, old_status=old_status)
```

Also update `_format_intent_summary` to handle the new intents:

```python
def _format_intent_summary(intent: str, guests: list[dict]) -> str:
    """Return a human-readable summary of what the system understood."""
    guest_names = [g["name"] for g in guests] if guests else []
    summaries = {
        "JOIN": "We've marked you as playing.",
        "DECLINE": "We've marked you as not playing.",
        "MAYBE": "We've marked you as maybe.",
        "BRING_GUESTS": f"We've marked you as playing with guest(s): {', '.join(guest_names)}.",
        "UPDATE_GUESTS": f"We've updated your guest list to: {', '.join(guest_names)}.",
        "QUERY_ROSTER": "You asked about the current roster.",
        "QUERY_PLAYER": "You asked about a player's status.",
        "GUEST_CONFIRM": f"We've confirmed guest(s) still attending: {', '.join(parsed.get('confirmed_guest_names', []))}.",
        "GUEST_DECLINE": "We've noted that your guests won't be attending.",
    }
    return summaries.get(intent, "We weren't sure what you meant.")
```

Note: `GUEST_CONFIRM` references `parsed` from outer scope. To avoid this, pass `confirmed_names` directly:

```python
def _format_intent_summary(intent: str, guests: list[dict], confirmed_names: list[str] | None = None) -> str:
    guest_names = [g["name"] for g in guests] if guests else []
    confirmed = confirmed_names or []
    summaries = {
        "JOIN": "We've marked you as playing.",
        "DECLINE": "We've marked you as not playing.",
        "MAYBE": "We've marked you as maybe.",
        "BRING_GUESTS": f"We've marked you as playing with guest(s): {', '.join(guest_names)}.",
        "UPDATE_GUESTS": f"We've updated your guest list to: {', '.join(guest_names)}.",
        "QUERY_ROSTER": "You asked about the current roster.",
        "QUERY_PLAYER": "You asked about a player's status.",
        "GUEST_CONFIRM": f"We've confirmed guest(s) still attending: {', '.join(confirmed)}.",
        "GUEST_DECLINE": "We've noted that your guests won't be attending.",
    }
    return summaries.get(intent, "We weren't sure what you meant.")
```

Update the call site:

```python
intent_summary = _format_intent_summary(
    intent,
    parsed.get("guests", []),
    parsed.get("confirmed_guest_names", []),
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_email_processor_handler.py::test_bring_guests_creates_player_entries -v
```

Expected: PASS.

- [ ] **Step 5: Run all unit tests**

```bash
make test-unit
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/email_processor/handler.py tests/unit/test_email_processor_handler.py
git commit -m "feat: update email_processor BRING_GUESTS and UPDATE_GUESTS to create guest Players entries"
```

---

## Task 9: Update `email_processor` — `DECLINE` with guests, `GUEST_CONFIRM`, `GUEST_DECLINE`

When a player declines and has guests in `playerStatus#YES`, move guests to `playerStatus#NO` and send the sponsor a follow-up. Handle the sponsor's reply with `GUEST_CONFIRM` and `GUEST_DECLINE`.

**Files:**
- Modify: `src/email_processor/handler.py`
- Modify: `tests/unit/test_email_processor_handler.py`

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_email_processor_handler.py`:

```python
@pytest.mark.unit
def test_decline_with_guests_moves_to_no_and_sends_followup():
    """DECLINE when player has guests: moves guests to NO, sends follow-up email."""
    from unittest.mock import MagicMock, patch

    bedrock_result = {
        "intent": "DECLINE",
        "guests": [],
        "confirmed_guest_names": [],
        "query_target": None,
        "reply_draft": "Sorry to hear that!",
    }

    yes_guests = [
        {"pk": "john@example.com", "sk": "guest#active", "name": "John",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
    ]
    raw_email = _make_raw_email("alice@example.com", "Re: Game", "Can't make it")

    with patch("email_processor.handler._get_s3_client") as mock_s3_fn, \
         patch("email_processor.handler.get_current_open_game") as mock_game, \
         patch("email_processor.handler.get_roster") as mock_roster, \
         patch("email_processor.handler.parse_player_email") as mock_parse, \
         patch("email_processor.handler.get_player_name") as mock_name, \
         patch("email_processor.handler.update_player_response") as mock_update, \
         patch("email_processor.handler.remove_sponsor_guests_from_status") as mock_remove, \
         patch("email_processor.handler.add_guests_to_game_status") as mock_add, \
         patch("email_processor.handler.send_email") as mock_send_email, \
         patch("email_processor.handler.send_guest_followup") as mock_followup:

        mock_s3_fn.return_value.get_object.return_value = {"Body": MagicMock(read=lambda: raw_email)}
        mock_game.return_value = {"gameDate": "2026-04-05"}
        mock_roster.return_value = {
            "YES": {"players": {"alice@example.com": {"name": "Alice"}}, "guests": yes_guests},
            "NO": {"players": {}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        }
        mock_parse.return_value = bedrock_result
        mock_name.return_value = "Alice"
        mock_remove.return_value = yes_guests

        from email_processor.handler import handler
        result = handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}]}, None)

    assert result["statusCode"] == 200
    mock_remove.assert_called_once_with("2026-04-05", "YES", "alice@example.com")
    mock_add.assert_called_once_with("2026-04-05", "NO", yes_guests)
    mock_followup.assert_called_once_with(
        sponsor_email="alice@example.com",
        sponsor_name="Alice",
        guest_names=["John"],
        game_date="2026-04-05",
    )


@pytest.mark.unit
def test_decline_without_guests_no_followup():
    """DECLINE when player has no guests: normal decline, no follow-up sent."""
    from unittest.mock import MagicMock, patch

    bedrock_result = {
        "intent": "DECLINE",
        "guests": [],
        "confirmed_guest_names": [],
        "query_target": None,
        "reply_draft": "Sorry to hear that!",
    }
    raw_email = _make_raw_email("alice@example.com", "Re: Game", "Can't make it")

    with patch("email_processor.handler._get_s3_client") as mock_s3_fn, \
         patch("email_processor.handler.get_current_open_game") as mock_game, \
         patch("email_processor.handler.get_roster") as mock_roster, \
         patch("email_processor.handler.parse_player_email") as mock_parse, \
         patch("email_processor.handler.get_player_name") as mock_name, \
         patch("email_processor.handler.update_player_response") as mock_update, \
         patch("email_processor.handler.remove_sponsor_guests_from_status") as mock_remove, \
         patch("email_processor.handler.send_email") as mock_send, \
         patch("email_processor.handler.send_guest_followup") as mock_followup:

        mock_s3_fn.return_value.get_object.return_value = {"Body": MagicMock(read=lambda: raw_email)}
        mock_game.return_value = {"gameDate": "2026-04-05"}
        mock_roster.return_value = {
            "YES": {"players": {"alice@example.com": {"name": "Alice"}}, "guests": []},
            "NO": {"players": {}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        }
        mock_parse.return_value = bedrock_result
        mock_name.return_value = "Alice"
        mock_remove.return_value = []

        from email_processor.handler import handler
        handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}]}, None)

    mock_followup.assert_not_called()


@pytest.mark.unit
def test_guest_confirm_moves_guests_to_yes():
    """GUEST_CONFIRM moves confirmed guests from NO to YES."""
    from unittest.mock import MagicMock, patch

    bedrock_result = {
        "intent": "GUEST_CONFIRM",
        "guests": [],
        "confirmed_guest_names": ["John"],
        "query_target": None,
        "reply_draft": "John is still coming!",
    }
    raw_email = _make_raw_email("alice@example.com", "Re: Your guests", "John is still coming")

    with patch("email_processor.handler._get_s3_client") as mock_s3_fn, \
         patch("email_processor.handler.get_current_open_game") as mock_game, \
         patch("email_processor.handler.get_roster") as mock_roster, \
         patch("email_processor.handler.parse_player_email") as mock_parse, \
         patch("email_processor.handler.get_player_name") as mock_name, \
         patch("email_processor.handler.move_confirmed_guests") as mock_move, \
         patch("email_processor.handler.send_email") as mock_send:

        mock_s3_fn.return_value.get_object.return_value = {"Body": MagicMock(read=lambda: raw_email)}
        mock_game.return_value = {"gameDate": "2026-04-05"}
        mock_roster.return_value = {
            "YES": {"players": {}, "guests": []},
            "NO": {"players": {"alice@example.com": {"name": "Alice"}}, "guests": [
                {"pk": "john@example.com", "sk": "guest#active", "name": "John",
                 "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
            ]},
            "MAYBE": {"players": {}, "guests": []},
        }
        mock_parse.return_value = bedrock_result
        mock_name.return_value = "Alice"

        from email_processor.handler import handler
        result = handler({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": "k"}}}]}, None)

    assert result["statusCode"] == 200
    mock_move.assert_called_once_with("2026-04-05", "alice@example.com", ["John"])
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_email_processor_handler.py::test_decline_with_guests_moves_to_no_and_sends_followup tests/unit/test_email_processor_handler.py::test_decline_without_guests_no_followup tests/unit/test_email_processor_handler.py::test_guest_confirm_moves_guests_to_yes -v
```

Expected: FAIL.

- [ ] **Step 3: Update DECLINE, add GUEST_CONFIRM and GUEST_DECLINE in `handler()`**

Replace the `DECLINE` block:

```python
elif intent == "DECLINE":
    player_name = get_player_name(sender_email)
    update_player_response(game_date, sender_email, "NO", name=player_name, old_status=old_status)
    # Move any guests this player brought from YES to NO
    sponsor_guests = remove_sponsor_guests_from_status(game_date, "YES", sender_email)
    if sponsor_guests:
        add_guests_to_game_status(game_date, "NO", sponsor_guests)
        guest_names = [g["name"] for g in sponsor_guests]
        send_guest_followup(
            sponsor_email=sender_email,
            sponsor_name=player_name,
            guest_names=guest_names,
            game_date=game_date,
        )
```

Add after the `UPDATE_GUESTS` block:

```python
elif intent == "GUEST_CONFIRM":
    confirmed_names = parsed.get("confirmed_guest_names", [])
    if confirmed_names:
        move_confirmed_guests(game_date, sender_email, confirmed_names)
elif intent == "GUEST_DECLINE":
    # Guests remain in NO — no action needed; game_finalizer will clean up
    logger.info(f"GUEST_DECLINE from {sender_email} — guests remain in NO")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_email_processor_handler.py::test_decline_with_guests_moves_to_no_and_sends_followup tests/unit/test_email_processor_handler.py::test_decline_without_guests_no_followup tests/unit/test_email_processor_handler.py::test_guest_confirm_moves_guests_to_yes -v
```

Expected: PASS.

- [ ] **Step 5: Run all unit tests**

```bash
make test-unit
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/email_processor/handler.py tests/unit/test_email_processor_handler.py
git commit -m "feat: handle guest follow-up flow in email_processor (DECLINE, GUEST_CONFIRM, GUEST_DECLINE)"
```

---

## Task 10: Update `game_finalizer` to clean up guest Players entries

After marking the game as PLAYED, read all guests from YES/NO/MAYBE and delete their Players table entries.

**Files:**
- Modify: `src/game_finalizer/handler.py`
- Modify: `tests/unit/test_game_finalizer.py`

- [ ] **Step 1: Write failing test**

In `tests/unit/test_game_finalizer.py`:

```python
@pytest.mark.unit
def test_game_finalizer_deletes_guest_entries():
    """game_finalizer deletes guest Players entries from YES, NO, and MAYBE."""
    from unittest.mock import MagicMock, patch

    game_date = "2026-04-05"
    yes_guests = [
        {"pk": "john@example.com", "sk": "guest#active", "name": "John",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
    ]
    no_guests = [
        {"pk": "bob@example.com", "sk": "guest#active#Jane", "name": "Jane",
         "sponsorEmail": "bob@example.com", "sponsorName": "Bob"},
    ]

    with patch("game_finalizer.handler.get_game_status") as mock_status, \
         patch("game_finalizer.handler.update_game_status") as mock_update, \
         patch("game_finalizer.handler.get_roster") as mock_roster, \
         patch("game_finalizer.handler.delete_guest_entries") as mock_delete:

        mock_status.return_value = {"gameDate": game_date, "status": "OPEN"}
        mock_roster.return_value = {
            "YES": {"players": {}, "guests": yes_guests},
            "NO": {"players": {}, "guests": no_guests},
            "MAYBE": {"players": {}, "guests": []},
        }

        from game_finalizer.handler import handler
        result = handler({}, None)

    assert result["statusCode"] == 200
    mock_update.assert_called_once_with(game_date, "PLAYED")
    mock_delete.assert_called_once()
    deleted = mock_delete.call_args[0][0]
    assert len(deleted) == 2
    pks = {g["pk"] for g in deleted}
    assert "john@example.com" in pks
    assert "bob@example.com" in pks


@pytest.mark.unit
def test_game_finalizer_no_guests_does_not_call_delete():
    """game_finalizer skips delete_guest_entries when no guests exist."""
    from unittest.mock import MagicMock, patch

    with patch("game_finalizer.handler.get_game_status") as mock_status, \
         patch("game_finalizer.handler.update_game_status") as mock_update, \
         patch("game_finalizer.handler.get_roster") as mock_roster, \
         patch("game_finalizer.handler.delete_guest_entries") as mock_delete:

        mock_status.return_value = {"gameDate": "2026-04-05", "status": "OPEN"}
        mock_roster.return_value = {
            "YES": {"players": {}, "guests": []},
            "NO": {"players": {}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        }

        from game_finalizer.handler import handler
        handler({}, None)

    mock_delete.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_game_finalizer.py::test_game_finalizer_deletes_guest_entries tests/unit/test_game_finalizer.py::test_game_finalizer_no_guests_does_not_call_delete -v
```

Expected: FAIL.

- [ ] **Step 3: Update `src/game_finalizer/handler.py`**

```python
import logging
from datetime import date
from typing import Any

from common.config import load_config
from common.dynamo import delete_guest_entries, get_game_status, get_roster, update_game_status

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

config = load_config()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler: mark today's game as PLAYED and clean up guest entries."""
    game_date = date.today().isoformat()
    logger.info(f"game_finalizer running for {game_date}")

    game = get_game_status(game_date)

    if game is None:
        logger.info(f"No game found for {game_date}, nothing to do")
        return {"statusCode": 200, "body": "No game found"}

    status = game.get("status")

    if status == "OPEN":
        update_game_status(game_date, "PLAYED")
        logger.info(f"Marked game {game_date} as PLAYED")

        roster = get_roster(game_date)
        all_guests = (
            roster["YES"]["guests"]
            + roster["NO"]["guests"]
            + roster["MAYBE"]["guests"]
        )
        if all_guests:
            delete_guest_entries(all_guests)
            logger.info(f"Deleted {len(all_guests)} guest Players entries for {game_date}")

        return {
            "statusCode": 200,
            "body": {
                "action": "game_marked_played",
                "gameDate": game_date,
                "guestsDeleted": len(all_guests),
            },
        }

    logger.info(f"Game {game_date} already has status {status}, no-op")
    return {
        "statusCode": 200,
        "body": {"action": "no_action", "gameDate": game_date, "status": status},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_game_finalizer.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run all unit tests**

```bash
make test-unit
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/game_finalizer/handler.py tests/unit/test_game_finalizer.py
git commit -m "feat: game_finalizer deletes guest Players entries after marking game PLAYED"
```

---

## Task 11: Update integration test fixtures and add integration tests

Update `seed_game` to use the new schema (add `guests: []`) and add end-to-end integration tests for the happy path and no-response path.

**Files:**
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_game_finalizer_flow.py`

- [ ] **Step 1: Update `seed_game` fixture in `tests/integration/conftest.py`**

Add `"guests": {"L": []}` to each `playerStatus#*` item in the batch write. Replace the three playerStatus PutRequest items:

```python
{
    "PutRequest": {
        "Item": {
            "gameDate": {"S": game_date},
            "sk": {"S": "playerStatus#YES"},
            "players": {
                "M": {
                    "alice@example.com": {"M": {"name": {"S": "Alice"}}},
                    "bob@example.com": {"M": {"name": {"S": "Bob"}}},
                    "charlie@example.com": {"M": {"name": {"S": "Charlie"}}},
                }
            },
            "guests": {"L": []},
        }
    }
},
{
    "PutRequest": {
        "Item": {
            "gameDate": {"S": game_date},
            "sk": {"S": "playerStatus#NO"},
            "players": {
                "M": {
                    "dave@example.com": {"M": {"name": {"S": "Dave"}}},
                }
            },
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
```

Also update the Players table creation in the session-scoped `dynamodb_tables` fixture. The integration Players table already uses `active` as the RANGE key — confirm this is unchanged.

- [ ] **Step 2: Run existing integration tests to verify nothing broke**

```bash
make test-integration
```

Expected: all existing integration tests pass.

- [ ] **Step 3: Write integration test — happy path guest flow**

In `tests/integration/test_game_finalizer_flow.py`, add:

```python
@pytest.mark.integration
def test_guest_cleanup_after_game_finalizer(
    dynamodb_tables, seed_players, ses_identity
):
    """Full flow: create game, add guests, run game_finalizer, verify guest entries deleted."""
    import boto3
    from unittest.mock import patch
    from datetime import date

    game_date = date.today().isoformat()

    # Create game
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

    # Run game_finalizer
    from game_finalizer.handler import handler
    with patch("game_finalizer.handler.date") as mock_date:
        mock_date.today.return_value = date.fromisoformat(game_date)
        result = handler({}, None)

    assert result["statusCode"] == 200
    assert result["body"]["action"] == "game_marked_played"
    assert result["body"]["guestsDeleted"] == 1

    # Verify guest entry deleted from Players table
    item_after = players_table.get_item(
        Key={"email": "testguest@example.com", "active": "guest#active"}
    ).get("Item")
    assert item_after is None

    # Cleanup
    from common.dynamo import get_game_status, update_game_status
    games_table = dynamodb_tables.Table("Games")
    for sk in ("gameStatus", "playerStatus#YES", "playerStatus#NO", "playerStatus#MAYBE"):
        games_table.delete_item(Key={"gameDate": game_date, "sk": sk})


@pytest.mark.integration
def test_decline_with_guests_moves_to_no(dynamodb_tables, seed_players, ses_identity):
    """Player declines: their guests move from YES to NO guests array."""
    from datetime import date
    from unittest.mock import patch, MagicMock
    import json

    game_date = "2026-05-10"  # Fixed date to avoid collision with other tests

    from common.dynamo import (
        create_game, add_guests_to_game_status, create_guest_entry,
        update_player_response, get_roster,
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
    from common.dynamo import remove_sponsor_guests_from_status, add_guests_to_game_status as add_guests
    update_player_response(game_date, "alice@example.com", "NO", name="Alice", old_status="YES")
    guests = remove_sponsor_guests_from_status(game_date, "YES", "alice@example.com")
    add_guests(game_date, "NO", guests)

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
```

- [ ] **Step 4: Run integration tests**

```bash
make test-integration
```

Expected: all pass including the two new tests.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/conftest.py tests/integration/test_game_finalizer_flow.py
git commit -m "feat: add guest integration tests and update seed_game fixture for new schema"
```

---

## Task 12: Final check — run all tests and verify

- [ ] **Step 1: Run all tests**

```bash
make test-all
```

Expected: all unit and integration tests pass.

- [ ] **Step 2: Commit if any minor fixes were needed**

```bash
git add -p  # stage only needed fixes
git commit -m "fix: address any issues found in final test run"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Guest entries created in Players table on BRING_GUESTS | Task 8 |
| Players table schema: PK=email, SK=guest#active or guest#active#name | Tasks 1, 5 |
| `guests` array on playerStatus items | Tasks 2, 5 |
| `sponsorEmail`, `sponsorName` on guest objects | Task 5 |
| DECLINE moves guests from YES to NO + sends follow-up | Task 9 |
| GUEST_CONFIRM moves named guests from NO to YES | Task 9 |
| GUEST_DECLINE is a no-op | Task 9 |
| Contact email stored on Games table object only (no Players update on GUEST_CONFIRM) | Task 9 — `move_confirmed_guests` does not touch Players table |
| UPDATE_GUESTS deletes old guest Players entries, creates new | Task 8 |
| game_finalizer cleans up guests from YES+NO+MAYBE | Task 10 |
| reminder_checker count uses new roster structure | Task 3 |
| send_confirmation uses new roster structure | Task 3 |
| New Bedrock intents GUEST_CONFIRM, GUEST_DECLINE | Task 7 |
| Integration test: happy path | Task 11 |
| Integration test: decline + guest moves | Task 11 |
