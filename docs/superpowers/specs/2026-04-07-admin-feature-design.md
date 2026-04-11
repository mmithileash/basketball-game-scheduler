# Admin Feature Design

**Date:** 2026-04-07  
**Status:** Approved

## Overview

Add admin functionality to the basketball game scheduler, allowing designated admins to cancel games and manage players entirely via email. Admins email a dedicated address (`admin@yourdomain.com`). A new `admin-processor` Lambda handles these commands.

---

## 1. Data Model Changes

### Players Table

Add an `isAdmin` boolean attribute to existing player records. The key structure (`email` as PK, `active` as SK) is unchanged.

**Active player record (existing + new field):**
```
{ email: "alice@x.com", active: "true", name: "Alice", isAdmin: true }
```

**Inactive player record:**
```
{ email: "alice@x.com", active: "false", name: "Alice", isAdmin: false }
```

Because `active` is the sort key, toggling active status requires a delete + put (DynamoDB does not allow updating key attributes in place). Deactivating: delete `{email, active="true"}`, put `{email, active="false", name, ...}`. Reactivating reverses this.

### New DynamoDB Operations (in `src/common/dynamo.py`)

- `add_player(email, name, is_admin=False)` — puts a new item with `active="true"`, `isAdmin=is_admin`
- `set_player_admin(email, is_admin)` — updates `isAdmin` on the active player record
- `deactivate_player(email)` — read active record (to preserve `name`, `isAdmin`, etc.), delete it, put new record with `active="false"` and all other attributes preserved
- `reactivate_player(email)` — read inactive record (to preserve attributes), delete it, put new record with `active="true"` and all other attributes preserved
- `is_admin(email)` — returns True if the player exists with `isAdmin=True`

### Games Table

No schema changes. Pre-cancellation reuses the existing `gameStatus` item shape:
```
{ gameDate: "2026-04-11", sk: "gameStatus", status: "CANCELLED", createdAt: "..." }
```

---

## 2. Admin Commands

Admins email `admin@yourdomain.com`. The `admin-processor` Lambda uses Bedrock to classify intent.

| Intent | Example body | Action |
|---|---|---|
| `CANCEL_GAME` | "Cancel the game on April 11" | Resolves the mentioned date to the nearest Saturday (rejects if date is ambiguous or not resolvable); pre-creates or updates `gameStatus` to `CANCELLED` for that date |
| `ADD_PLAYER` | "Add player alice@x.com, name Alice" | Creates player record with `isAdmin=False` |
| `ADD_ADMIN` | "Add admin bob@x.com, name Bob" | Creates player record with `isAdmin=True` |
| `DEACTIVATE_PLAYER` | "Deactivate alice@x.com" | Delete+recreate with `active="false"` |
| `REACTIVATE_PLAYER` | "Reactivate alice@x.com" | Delete+recreate with `active="true"` |

**Authorization:** The Lambda calls `is_admin(sender_email)` first. Non-admins get a polite rejection email. Admins receive a confirmation reply summarising the action taken (or an error description if the command wasn't understood).

**Bedrock prompt:** A dedicated system prompt in `src/common/bedrock_client.py` classifies admin intents — separate from the existing player-facing prompt. Returns structured JSON: `{intent, game_date, email, name, is_admin}`.

---

## 3. Cancellation Flow

### A. Admin cancels before Monday (advance cancellation)

1. Admin emails "Cancel game on April 11"
2. `admin-processor` writes `{gameDate: "2026-04-11", sk: "gameStatus", status: "CANCELLED"}` to DynamoDB (pre-creates the record)
3. Monday 9AM — `announcement-sender` calls `get_game_status()` before creating a game:
   - If status is `CANCELLED`: sends a "no game this week" email to all active players; does not create a new game record
   - If no record exists: proceeds normally (creates game, sends announcement)
4. `reminder-checker` on Wednesday/Friday sees `CANCELLED` status and sends no emails

### B. Admin cancels after Monday announcement (mid-week cancellation)

1. Game is already `OPEN`; some players may have RSVP'd
2. Admin emails "Cancel game on April 11"
3. `admin-processor` calls `update_game_status()` → `CANCELLED`
4. `admin-processor` emails all players who responded YES or MAYBE informing them the game is cancelled
5. `reminder-checker` on Wednesday/Friday sees `CANCELLED` and skips
6. `email-processor` already handles inbound replies for `CANCELLED` games correctly (replies "game is cancelled")

---

## 4. New Lambda: `admin-processor`

**Location:** `src/admin_processor/handler.py`

**Flow:**
1. Read raw email from S3 (same pattern as `email-processor`)
2. Extract sender email
3. Call `is_admin(sender_email)` — reject if not admin
4. Parse body via Bedrock admin prompt to get intent + parameters
5. Execute the command (DynamoDB write, optional SES broadcast)
6. Send confirmation reply to admin

---

## 5. Changes to Existing Lambdas

### `announcement-sender`

Before creating a game, call `get_game_status(saturday)`:
- `status == "CANCELLED"` → send "no game this week" broadcast to all active players, return early
- No record → proceed as normal (create game, send announcement)

### `reminder-checker`

After fetching the upcoming game, check status:
- `status == "CANCELLED"` → log and return early (no emails)
- Proceed as normal otherwise

---

## 6. Infrastructure (Terraform)

New resources:

- **SES receipt rule** — routes `admin@yourdomain.com` to S3 prefix `admin/` in the existing email bucket
- **S3 event notification** — triggers `admin-processor` Lambda on new objects under `admin/` prefix
- **Lambda function** — `admin-processor`, same runtime (Python 3.12) and layer pattern as `email-processor`
- **IAM role** — SES send + DynamoDB read/write on Players and Games tables + S3 read on email bucket + Bedrock invoke

Updated resources:

- **`announcement-sender` Lambda env vars** — no new vars needed; logic change only
- **`reminder-checker` Lambda env vars** — no new vars needed; logic change only
- **`terraform.tfvars`** — add `admin_email` variable (e.g. `admin@yourdomain.com`)

---

## 7. Testing

**Unit tests** (`tests/unit/test_admin_processor.py`):
- Non-admin sender is rejected
- `CANCEL_GAME` pre-creates a CANCELLED record when no game exists
- `CANCEL_GAME` updates status and triggers broadcast when game is OPEN
- `ADD_PLAYER` creates correct record
- `ADD_ADMIN` creates correct record with `isAdmin=True`
- `DEACTIVATE_PLAYER` / `REACTIVATE_PLAYER` perform correct delete+put

**Unit tests** (`tests/unit/test_announcement_sender.py`):
- Pre-cancelled game triggers "no game this week" broadcast, no new game created

**Unit tests** (`tests/unit/test_reminder_checker.py`):
- Cancelled game causes early return with no emails sent

All unit tests use `moto` — no Docker required.
