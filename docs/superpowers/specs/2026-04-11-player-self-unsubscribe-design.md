# Player Self-Unsubscribe Design

**Date:** 2026-04-11
**Status:** Approved

## Overview

Allow registered players to unsubscribe themselves from future game announcements by clicking a mailto link included in all player-facing emails. Clicking the link opens the player's email client with a pre-composed unsubscribe email; sending it triggers deactivation via the existing `email_processor` Lambda — no Bedrock call required.

---

## Architecture

No new infrastructure is required. The feature is handled entirely within the existing `email_processor` Lambda and `email_service` module, plus a small DRY fix extracting a shared utility.

### Components changed

| File | Change |
|---|---|
| `src/common/email_utils.py` | New module — shared `_extract_sender_email` helper |
| `src/common/email_service.py` | Add unsubscribe footer helper; append to all player-facing `send_*` functions |
| `src/email_processor/handler.py` | Import from `email_utils`; add early-return UNSUBSCRIBE branch |
| `src/admin_processor/handler.py` | Import `_extract_sender_email` from `email_utils` |

---

## Data Flow

```
Player clicks mailto link in email
  → email client composes: To: <game-address>, Subject: UNSUBSCRIBE
  → player sends email
  → SES receipt rule stores email in S3
  → email_processor Lambda triggered
  → subject.strip().upper() == "UNSUBSCRIBE" → early-return branch
  → get_sender_role(sender_email) → must be "player"
  → deactivate_player(sender_email)  [active: "true" → "false"]
  → send confirmation email to player
  → return 200
```

---

## Detailed Design

### 1. `src/common/email_utils.py` (new)

Extract the `_extract_sender_email` function that is currently copy-pasted identically in both `email_processor/handler.py` and `admin_processor/handler.py`.

```python
def extract_sender_email(from_header: str) -> str:
    """Extract the email address from a From header value."""
    if "<" in from_header and ">" in from_header:
        return from_header.split("<")[1].split(">")[0].strip()
    return from_header.strip()
```

Both handlers import this instead of defining it locally.

### 2. `src/common/email_service.py`

**New private helper:**

```python
def _unsubscribe_footer(sender_email: str) -> str
```

Returns a plain-text block containing the mailto unsubscribe link:

```
---
To unsubscribe from future game emails, click the link below (or copy it into your browser):
mailto:<game-reply-address>?subject=UNSUBSCRIBE
```

The game reply address comes from `config.sender_email` (already available via `_get_config()`).

**Functions updated to append the footer (players only):**
- `send_announcement`
- `send_reminder`
- `send_cancellation`
- `send_confirmation`
- `send_no_game_announcement`
- `send_guest_followup` (sent to sponsor, who is a player)
- `send_guest_cancelled_sponsor_notification` (sent to sponsor, who is a player)

**`send_admin_cancelled_broadcast`** receives an `include_unsubscribe: bool = False` parameter. In `admin_processor`, callers pass `True` when notifying players and `False` (default) when notifying guests.

### 3. `src/email_processor/handler.py`

Add an early-return branch immediately after `sender_email` and `subject` are extracted, before `get_sender_role` is called:

```python
if subject.strip().upper() == "UNSUBSCRIBE":
    role = get_sender_role(sender_email)
    if role != "player":
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

The unsubscribe confirmation email does **not** include an unsubscribe footer (the player is already deactivated).

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Email from unknown/guest address | Reply with "no active account found" error, return 403 |
| Player already inactive (`deactivate_player` raises `ValueError`) | Log warning, send "already unsubscribed" reply, return 200 |

---

## What is NOT changing

- No new Lambda, SES rule, or Terraform changes.
- No Bedrock call for unsubscribe — purely deterministic subject-line check.
- Admin is not notified of self-unsubscribes.
- Current-week game roster entry is not modified; player deactivation only affects future `get_active_players()` calls.
- Guests cannot unsubscribe via this mechanism; the unsubscribe footer is not included in guest-facing emails.

---

## Testing

**Unit tests to add (`tests/unit/`):**
- `test_email_service.py` — assert unsubscribe footer appears in all player-facing send functions; assert it does not appear in guest-facing paths
- `test_email_processor.py` — assert UNSUBSCRIBE subject triggers deactivation and confirmation reply; assert unknown/guest sender receives error; assert already-inactive player is handled gracefully
- `test_email_utils.py` — assert `extract_sender_email` parses both `Name <email>` and bare `email` formats
