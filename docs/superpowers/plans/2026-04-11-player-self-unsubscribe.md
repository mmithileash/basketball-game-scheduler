# Player Self-Unsubscribe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow registered players to unsubscribe themselves by clicking a mailto link in any player-facing email, which sends an email that the `email_processor` Lambda detects and handles deterministically (no Bedrock call).

**Architecture:** A new shared `email_utils.py` extracts the duplicated `_extract_sender_email` helper. `email_service.py` gains a private `_unsubscribe_footer()` function appended to all player-facing send functions. `email_processor` adds an early-return branch that checks `subject.strip().upper() == "UNSUBSCRIBE"`, calls `deactivate_player`, and replies — before any Bedrock call.

**Tech Stack:** Python 3.12, AWS Lambda, boto3 (DynamoDB + SES), moto (unit tests), pytest

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/common/email_utils.py` | Create | Shared `extract_sender_email` helper |
| `src/common/email_service.py` | Modify | Add `_unsubscribe_footer()`, update 7 player-facing functions, add `include_unsubscribe` param to `send_admin_cancelled_broadcast` |
| `src/email_processor/handler.py` | Modify | Import `extract_sender_email` from `email_utils`; import `deactivate_player`; add UNSUBSCRIBE early-return branch |
| `src/admin_processor/handler.py` | Modify | Import `extract_sender_email` from `email_utils`; pass `include_unsubscribe=True` for player cancellation notifications |
| `tests/unit/test_email_utils.py` | Create | Tests for `extract_sender_email` |
| `tests/unit/test_email_service.py` | Modify | Add tests: unsubscribe footer present in player functions, absent in guest-only path |
| `tests/unit/test_email_processor_handler.py` | Modify | Update import of `extract_sender_email`; add UNSUBSCRIBE handler tests |

---

## Task 1: Extract `extract_sender_email` to `src/common/email_utils.py`

**Files:**
- Create: `src/common/email_utils.py`
- Create: `tests/unit/test_email_utils.py`
- Modify: `src/email_processor/handler.py`
- Modify: `src/admin_processor/handler.py`
- Modify: `tests/unit/test_email_processor_handler.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_email_utils.py`:

```python
import pytest

from common.email_utils import extract_sender_email


@pytest.mark.unit
def test_extract_plain_email():
    assert extract_sender_email("alice@example.com") == "alice@example.com"


@pytest.mark.unit
def test_extract_email_with_name():
    assert extract_sender_email("Alice <alice@example.com>") == "alice@example.com"


@pytest.mark.unit
def test_extract_email_with_name_and_spaces():
    assert extract_sender_email("  Alice Smith <alice@example.com>  ") == "alice@example.com"


@pytest.mark.unit
def test_extract_bare_email_strips_whitespace():
    assert extract_sender_email("  alice@example.com  ") == "alice@example.com"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/mithil/Source/basketball-game-scheduler
pytest tests/unit/test_email_utils.py -v
```

Expected: `ModuleNotFoundError: No module named 'common.email_utils'`

- [ ] **Step 3: Create `src/common/email_utils.py`**

```python
def extract_sender_email(from_header: str) -> str:
    """Extract the email address from a From header value.

    Handles both 'Name <email@example.com>' and bare 'email@example.com' formats.
    """
    if "<" in from_header and ">" in from_header:
        return from_header.split("<")[1].split(">")[0].strip()
    return from_header.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_email_utils.py -v
```

Expected: 4 passed

- [ ] **Step 5: Update `src/email_processor/handler.py` to use shared helper**

Remove the local `_extract_sender_email` function (lines 119–124) and update the import block at the top of the file. Replace:

```python
from common.bedrock_client import parse_player_email
from common.config import load_config
from common.dynamo import (
```

With:

```python
from common.bedrock_client import parse_player_email
from common.config import load_config
from common.dynamo import (
```

Add this import after the existing `from common.` imports:

```python
from common.email_utils import extract_sender_email
```

Then replace the call site at the line that reads:

```python
    sender_email = _extract_sender_email(from_header)
```

With:

```python
    sender_email = extract_sender_email(from_header)
```

Then delete the entire `_extract_sender_email` function definition (the private version) from the file.

- [ ] **Step 6: Update `src/admin_processor/handler.py` to use shared helper**

Add the import after the existing `from common.` imports:

```python
from common.email_utils import extract_sender_email
```

Replace the call site:

```python
    sender_email = _extract_sender_email(from_header)
```

With:

```python
    sender_email = extract_sender_email(from_header)
```

Then delete the entire local `_extract_sender_email` function definition from `admin_processor/handler.py`.

- [ ] **Step 7: Update the import in `tests/unit/test_email_processor_handler.py`**

Line 8 currently reads:

```python
from email_processor.handler import handler, _extract_email_body, _extract_sender_email
```

Change it to:

```python
from common.email_utils import extract_sender_email
from email_processor.handler import handler, _extract_email_body
```

Then search the test file for any direct calls to `_extract_sender_email(...)` and change them to `extract_sender_email(...)`.

- [ ] **Step 8: Run all unit tests to verify nothing is broken**

```bash
pytest tests/unit/ -v
```

Expected: all previously passing tests still pass

- [ ] **Step 9: Commit**

```bash
git add src/common/email_utils.py tests/unit/test_email_utils.py \
        src/email_processor/handler.py src/admin_processor/handler.py \
        tests/unit/test_email_processor_handler.py
git commit -m "refactor: extract shared extract_sender_email to common/email_utils"
```

---

## Task 2: Add unsubscribe footer to player-facing emails in `email_service.py`

**Files:**
- Modify: `src/common/email_service.py`
- Modify: `tests/unit/test_email_service.py`

- [ ] **Step 1: Write failing tests**

Add the following tests to `tests/unit/test_email_service.py`:

```python
@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_announcement(mocker):
    """send_announcement body contains the mailto unsubscribe link."""
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_announcement("player@example.com", "Alice", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_reminder(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_reminder("player@example.com", "Alice", 4, "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_cancellation(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_cancellation("player@example.com", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_confirmation(mocker):
    _setup_ses()
    roster = {
        "YES": {"players": {"player@example.com": {"name": "Alice"}}, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    }
    mock_send = mocker.patch("common.email_service.send_email")
    send_confirmation("player@example.com", "2026-04-12", roster)
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_no_game_announcement(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_no_game_announcement("player@example.com", "Alice", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_guest_followup(mocker):
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_guest_followup("sponsor@example.com", "Alice", ["John"], "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_in_guest_cancelled_sponsor_notification(mocker):
    from common.email_service import send_guest_cancelled_sponsor_notification
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_guest_cancelled_sponsor_notification("sponsor@example.com", "Alice", "John", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_absent_when_not_requested(mocker):
    """send_admin_cancelled_broadcast without include_unsubscribe has no footer."""
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_admin_cancelled_broadcast("player@example.com", "2026-04-12")
    body = mock_send.call_args[0][2]
    assert "UNSUBSCRIBE" not in body


@pytest.mark.unit
@mock_aws
def test_unsubscribe_footer_present_when_requested(mocker):
    """send_admin_cancelled_broadcast with include_unsubscribe=True includes footer."""
    _setup_ses()
    mock_send = mocker.patch("common.email_service.send_email")
    send_admin_cancelled_broadcast("player@example.com", "2026-04-12", include_unsubscribe=True)
    body = mock_send.call_args[0][2]
    assert "mailto:scheduler@example.com?subject=UNSUBSCRIBE" in body
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_email_service.py -v -k "unsubscribe"
```

Expected: all 9 unsubscribe tests fail (footer not present yet)

- [ ] **Step 3: Add `_unsubscribe_footer` to `src/common/email_service.py`**

Add this private helper function after the `_get_ses_client` function and before `send_email`:

```python
def _unsubscribe_footer() -> str:
    """Return a plain-text unsubscribe block to append to player emails."""
    config = _get_config()
    return (
        f"\n\n---\n"
        f"To unsubscribe from future game emails, click the link below "
        f"(this will open your email client with a pre-filled message):\n"
        f"mailto:{config.sender_email}?subject=UNSUBSCRIBE\n"
    )
```

- [ ] **Step 4: Update the 7 player-facing send functions**

In each of the following functions, append `_unsubscribe_footer()` to the `body` string just before the `send_email(...)` call.

**`send_announcement`** — change the `send_email` call to:

```python
    send_email(player_email, subject, body + _unsubscribe_footer())
```

**`send_reminder`** — change the `send_email` call to:

```python
    send_email(player_email, subject, body + _unsubscribe_footer())
```

**`send_cancellation`** — change the `send_email` call to:

```python
    send_email(player_email, subject, body + _unsubscribe_footer())
```

**`send_confirmation`** — change the `send_email` call to:

```python
    send_email(player_email, subject, body + _unsubscribe_footer())
```

**`send_no_game_announcement`** — change the `send_email` call to:

```python
    send_email(player_email, subject, body + _unsubscribe_footer())
```

**`send_guest_followup`** — change the `send_email` call to:

```python
    send_email(sponsor_email, subject, body + _unsubscribe_footer())
```

**`send_guest_cancelled_sponsor_notification`** — change the `send_email` call to:

```python
    send_email(sponsor_email, subject, body + _unsubscribe_footer())
```

- [ ] **Step 5: Update `send_admin_cancelled_broadcast` with `include_unsubscribe` parameter**

Change the function signature and body to:

```python
def send_admin_cancelled_broadcast(player_email: str, game_date: str, include_unsubscribe: bool = False) -> None:
    """Notify a player that an already-announced game has been cancelled by admin."""
    subject = f"Cancelled: Basketball Game - {game_date}"
    body = (
        f"Hi,\n\n"
        f"The basketball game scheduled for {game_date} has been cancelled by the organiser.\n\n"
        f"See you next week!\n"
    )
    if include_unsubscribe:
        body += _unsubscribe_footer()
    send_email(player_email, subject, body)
```

- [ ] **Step 6: Run the unsubscribe footer tests**

```bash
pytest tests/unit/test_email_service.py -v -k "unsubscribe"
```

Expected: all 9 tests pass

- [ ] **Step 7: Run full email_service tests to check no regressions**

```bash
pytest tests/unit/test_email_service.py -v
```

Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/common/email_service.py tests/unit/test_email_service.py
git commit -m "feat: add unsubscribe footer to all player-facing emails"
```

---

## Task 3: Update `admin_processor` to pass `include_unsubscribe=True` for player cancellation notifications

**Files:**
- Modify: `src/admin_processor/handler.py`
- Modify: `tests/unit/test_admin_processor.py`

- [ ] **Step 1: Write a failing test**

Add the following test to `tests/unit/test_admin_processor.py`:

```python
@pytest.mark.unit
def test_cancel_game_broadcast_includes_unsubscribe_for_players(mocker):
    """Players notified of admin cancellation receive the unsubscribe footer."""
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-04-19",
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mocker.patch("admin_processor.handler.get_game_status",
                 return_value={"status": "OPEN"})
    mocker.patch("admin_processor.handler.update_game_status")
    mocker.patch("admin_processor.handler.get_roster", return_value={
        "YES": {
            "players": {"alice@example.com": {"name": "Alice"}},
            "guests": [
                {"pk": "john@example.com", "sk": "guest#active", "name": "John",
                 "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
            ],
        },
        "MAYBE": {"players": {}, "guests": []},
    })
    mock_broadcast = mocker.patch("admin_processor.handler.send_admin_cancelled_broadcast")
    mocker.patch("admin_processor.handler.send_email")
    mock_s3 = mocker.patch("admin_processor.handler._get_s3_client")
    from unittest.mock import MagicMock
    mock_s3.return_value.get_object.return_value = {
        "Body": MagicMock(read=lambda: (
            b"From: admin@example.com\r\nTo: x\r\nSubject: Cancel\r\n\r\nCancel 2026-04-19"
        ))
    }

    handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    # Player call must include include_unsubscribe=True
    player_calls = [c for c in mock_broadcast.call_args_list if c[0][0] == "alice@example.com"]
    assert len(player_calls) == 1
    assert player_calls[0][1].get("include_unsubscribe") is True or player_calls[0][0][2] is True

    # Guest call must NOT include include_unsubscribe=True
    guest_calls = [c for c in mock_broadcast.call_args_list if c[0][0] == "john@example.com"]
    assert len(guest_calls) == 1
    kw = guest_calls[0][1]
    assert not kw.get("include_unsubscribe", False)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_admin_processor.py::test_cancel_game_broadcast_includes_unsubscribe_for_players -v
```

Expected: FAIL

- [ ] **Step 3: Update the player notification loop in `src/admin_processor/handler.py`**

In the `CANCEL_GAME` branch, find the block that iterates over `YES` and `MAYBE` statuses. Change:

```python
            notified: set[str] = set()
            for status_key in ("YES", "MAYBE"):
                for player_email in roster.get(status_key, {}).get("players", {}).keys():
                    send_admin_cancelled_broadcast(player_email, game_date)
                    notified.add(player_email)
                for guest in roster.get(status_key, {}).get("guests", []):
                    if guest.get("sk") == "guest#active":
                        send_admin_cancelled_broadcast(guest["pk"], game_date)
                        notified.add(guest["pk"])
```

To:

```python
            notified: set[str] = set()
            for status_key in ("YES", "MAYBE"):
                for player_email in roster.get(status_key, {}).get("players", {}).keys():
                    send_admin_cancelled_broadcast(player_email, game_date, include_unsubscribe=True)
                    notified.add(player_email)
                for guest in roster.get(status_key, {}).get("guests", []):
                    if guest.get("sk") == "guest#active":
                        send_admin_cancelled_broadcast(guest["pk"], game_date)
                        notified.add(guest["pk"])
```

- [ ] **Step 4: Run the new test**

```bash
pytest tests/unit/test_admin_processor.py::test_cancel_game_broadcast_includes_unsubscribe_for_players -v
```

Expected: PASS

- [ ] **Step 5: Run full admin_processor tests**

```bash
pytest tests/unit/test_admin_processor.py -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/admin_processor/handler.py tests/unit/test_admin_processor.py
git commit -m "feat: pass include_unsubscribe=True for player cancellation notifications"
```

---

## Task 4: Add UNSUBSCRIBE handler in `email_processor`

**Files:**
- Modify: `src/email_processor/handler.py`
- Modify: `tests/unit/test_email_processor_handler.py`

- [ ] **Step 1: Write failing tests**

Add the following tests to `tests/unit/test_email_processor_handler.py`:

```python
@pytest.mark.unit
def test_handler_unsubscribe_active_player(mocker):
    """UNSUBSCRIBE subject from an active player deactivates them and sends confirmation."""
    import io
    from email.mime.text import MIMEText
    msg = MIMEText("", "plain", "utf-8")
    msg["From"] = "alice@example.com"
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = "UNSUBSCRIBE"
    raw_email = msg.as_bytes()

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
    mock_deactivate = mocker.patch("email_processor.handler.deactivate_player")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"] == "Unsubscribed"
    mock_deactivate.assert_called_once_with("alice@example.com")
    mock_send.assert_called_once()
    confirmation_body = mock_send.call_args[0][2]
    assert "unsubscribed" in confirmation_body.lower()
    assert "organiser" in confirmation_body.lower()


@pytest.mark.unit
def test_handler_unsubscribe_case_insensitive(mocker):
    """Subject 'unsubscribe' (lowercase) triggers the same handler."""
    import io
    from email.mime.text import MIMEText
    msg = MIMEText("", "plain", "utf-8")
    msg["From"] = "alice@example.com"
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = "unsubscribe"
    raw_email = msg.as_bytes()

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
    mocker.patch("email_processor.handler.deactivate_player")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"] == "Unsubscribed"


@pytest.mark.unit
def test_handler_unsubscribe_unknown_sender(mocker):
    """UNSUBSCRIBE from an unregistered address returns 403 and sends error reply."""
    import io
    from email.mime.text import MIMEText
    msg = MIMEText("", "plain", "utf-8")
    msg["From"] = "nobody@example.com"
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = "UNSUBSCRIBE"
    raw_email = msg.as_bytes()

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="unknown")
    mock_deactivate = mocker.patch("email_processor.handler.deactivate_player")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 403
    mock_deactivate.assert_not_called()
    mock_send.assert_called_once()
    error_body = mock_send.call_args[0][2]
    assert "active player account" in error_body.lower() or "not found" in error_body.lower()


@pytest.mark.unit
def test_handler_unsubscribe_guest_sender(mocker):
    """UNSUBSCRIBE from a guest address returns 403 and sends error reply."""
    import io
    from email.mime.text import MIMEText
    msg = MIMEText("", "plain", "utf-8")
    msg["From"] = "guest@example.com"
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = "UNSUBSCRIBE"
    raw_email = msg.as_bytes()

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="guest")
    mock_deactivate = mocker.patch("email_processor.handler.deactivate_player")
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 403
    mock_deactivate.assert_not_called()


@pytest.mark.unit
def test_handler_unsubscribe_already_inactive(mocker):
    """UNSUBSCRIBE when deactivate_player raises ValueError returns 200 with 'already' message."""
    import io
    from email.mime.text import MIMEText
    msg = MIMEText("", "plain", "utf-8")
    msg["From"] = "alice@example.com"
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = "UNSUBSCRIBE"
    raw_email = msg.as_bytes()

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
    mocker.patch("email_processor.handler.deactivate_player",
                 side_effect=ValueError("No active player found"))
    mock_send = mocker.patch("email_processor.handler.send_email")

    result = handler(_make_s3_event(), None)

    assert result["statusCode"] == 200
    assert result["body"] == "Already inactive"
    mock_send.assert_called_once()
    body = mock_send.call_args[0][2]
    assert "already" in body.lower()


@pytest.mark.unit
def test_handler_unsubscribe_does_not_call_bedrock(mocker):
    """UNSUBSCRIBE must short-circuit before any Bedrock call."""
    import io
    from email.mime.text import MIMEText
    msg = MIMEText("", "plain", "utf-8")
    msg["From"] = "alice@example.com"
    msg["To"] = "scheduler@example.com"
    msg["Subject"] = "UNSUBSCRIBE"
    raw_email = msg.as_bytes()

    mock_s3 = mocker.MagicMock()
    mock_s3.get_object.return_value = {"Body": io.BytesIO(raw_email)}
    mocker.patch("email_processor.handler._get_s3_client", return_value=mock_s3)
    mocker.patch("email_processor.handler.get_sender_role", return_value="player")
    mocker.patch("email_processor.handler.deactivate_player")
    mocker.patch("email_processor.handler.send_email")
    mock_bedrock = mocker.patch("email_processor.handler.parse_player_email")

    handler(_make_s3_event(), None)

    mock_bedrock.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/unit/test_email_processor_handler.py -v -k "unsubscribe"
```

Expected: all 6 fail (no UNSUBSCRIBE branch exists yet)

- [ ] **Step 3: Add `deactivate_player` to `email_processor/handler.py` imports**

The import block already imports from `common.dynamo`. Add `deactivate_player` to that block:

```python
from common.dynamo import (
    add_guests_to_game_status,
    create_guest_entry,
    deactivate_player,
    delete_guest_entries,
    get_player_name,
    get_roster,
    get_sender_role,
    get_upcoming_game,
    move_confirmed_guests,
    remove_guest_from_status,
    remove_sponsor_guests_from_status,
    update_player_response,
)
```

- [ ] **Step 4: Add the UNSUBSCRIBE early-return branch to `src/email_processor/handler.py`**

In the `handler` function, after these lines:

```python
    sender_email = extract_sender_email(from_header)
    logger.info("Email from %s, subject: %s", sender_email, subject)
```

Add the UNSUBSCRIBE branch immediately after:

```python
    if subject.strip().upper() == "UNSUBSCRIBE":
        role = get_sender_role(sender_email)
        if role != "player":
            logger.warning(f"Unsubscribe attempt from non-player: {sender_email} (role={role})")
            send_email(
                sender_email,
                "Re: UNSUBSCRIBE",
                "We couldn't find an active player account for this email address. "
                "Please contact the organiser if you believe this is an error.",
            )
            return {"statusCode": 403, "body": "Not an active player"}

        try:
            deactivate_player(sender_email)
        except ValueError:
            logger.warning(f"Player {sender_email} attempted self-unsubscribe but is already inactive")
            send_email(
                sender_email,
                "Re: UNSUBSCRIBE",
                "You are already unsubscribed from game announcements. "
                "Please contact the organiser if you'd like to rejoin.",
            )
            return {"statusCode": 200, "body": "Already inactive"}

        send_email(
            sender_email,
            "You've been unsubscribed",
            "You've been successfully unsubscribed from future basketball game announcements.\n\n"
            "If you'd like to rejoin, please contact the organiser.",
        )
        logger.info(f"Player {sender_email} self-unsubscribed")
        return {"statusCode": 200, "body": "Unsubscribed"}
```

- [ ] **Step 5: Run the new tests**

```bash
pytest tests/unit/test_email_processor_handler.py -v -k "unsubscribe"
```

Expected: all 6 pass

- [ ] **Step 6: Run full email_processor tests to check no regressions**

```bash
pytest tests/unit/test_email_processor_handler.py -v
```

Expected: all tests pass

- [ ] **Step 7: Run all unit tests**

```bash
pytest tests/unit/ -v
```

Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/email_processor/handler.py tests/unit/test_email_processor_handler.py
git commit -m "feat: add self-serve UNSUBSCRIBE handler in email_processor"
```
