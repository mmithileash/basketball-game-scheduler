from datetime import date
from unittest.mock import call

import pytest

from reminder_checker.handler import handler, _count_confirmed


@pytest.mark.unit
def test_wednesday_below_minimum(mocker):
    """Mock roster with 4 confirmed, verify reminders sent to pending."""
    mocker.patch(
        "reminder_checker.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 25)},  # Wednesday
    )
    mocker.patch(
        "reminder_checker.handler.get_current_open_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch(
        "reminder_checker.handler.get_roster",
        return_value={
            "YES": {
                "players": {
                    "alice@example.com": {"name": "Alice"},
                    "bob@example.com": {"name": "Bob"},
                    "charlie@example.com": {"name": "Charlie"},
                },
                "guests": [{"name": "guest1", "sponsorName": "Charlie"}],
            },
            "NO": {
                "players": {"dave@example.com": {"name": "Dave"}},
                "guests": [],
            },
            "MAYBE": {"players": {}, "guests": []},
        },
    )
    mocker.patch(
        "reminder_checker.handler.get_pending_players",
        return_value=[
            {"email": "eve@example.com", "name": "Eve"},
            {"email": "frank@example.com", "name": None},
        ],
    )
    mock_reminder = mocker.patch("reminder_checker.handler.send_reminder")

    result = handler({}, None)

    assert result["body"]["action"] == "reminders_sent"
    assert result["body"]["confirmedCount"] == 4  # 3 players + 1 guest
    assert mock_reminder.call_count == 2
    mock_reminder.assert_any_call("eve@example.com", "Eve", 4, "2026-03-28")
    mock_reminder.assert_any_call("frank@example.com", None, 4, "2026-03-28")


@pytest.mark.unit
def test_wednesday_above_minimum(mocker):
    """Verify no action taken when above minimum on Wednesday."""
    mocker.patch(
        "reminder_checker.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 25)},  # Wednesday
    )
    mocker.patch(
        "reminder_checker.handler.get_current_open_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch(
        "reminder_checker.handler.get_roster",
        return_value={
            "YES": {
                "players": {f"player{i}@example.com": {"name": f"Player{i}"} for i in range(7)},
                "guests": [],
            },
            "NO": {"players": {}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        },
    )
    mock_reminder = mocker.patch("reminder_checker.handler.send_reminder")

    result = handler({}, None)

    assert result["body"]["action"] == "no_action"
    mock_reminder.assert_not_called()


@pytest.mark.unit
def test_friday_below_minimum(mocker):
    """Verify cancellation sent to all, game marked CANCELLED."""
    mocker.patch(
        "reminder_checker.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 27)},  # Friday
    )
    mocker.patch(
        "reminder_checker.handler.get_current_open_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch(
        "reminder_checker.handler.get_roster",
        return_value={
            "YES": {
                "players": {
                    "alice@example.com": {"name": "Alice"},
                    "bob@example.com": {"name": "Bob"},
                },
                "guests": [],
            },
            "NO": {
                "players": {"charlie@example.com": {"name": "Charlie"}},
                "guests": [],
            },
            "MAYBE": {"players": {}, "guests": []},
        },
    )
    mocker.patch(
        "reminder_checker.handler.get_pending_players",
        return_value=[
            {"email": "dave@example.com", "name": "Dave"},
        ],
    )
    mock_update_status = mocker.patch("reminder_checker.handler.update_game_status")
    mock_cancel = mocker.patch("reminder_checker.handler.send_cancellation")

    result = handler({}, None)

    assert result["body"]["action"] == "game_cancelled"
    mock_update_status.assert_called_once_with("2026-03-28", "CANCELLED")

    # Should send cancellation to all roster players + pending
    cancelled_emails = {c[0][0] for c in mock_cancel.call_args_list}
    assert "alice@example.com" in cancelled_emails
    assert "bob@example.com" in cancelled_emails
    assert "charlie@example.com" in cancelled_emails
    assert "dave@example.com" in cancelled_emails


@pytest.mark.unit
def test_friday_above_minimum(mocker):
    """Verify confirmation sent to confirmed players."""
    mocker.patch(
        "reminder_checker.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 27)},  # Friday
    )
    mocker.patch(
        "reminder_checker.handler.get_current_open_game",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )

    roster = {
        "YES": {
            "players": {f"player{i}@example.com": {"name": f"Player{i}"} for i in range(7)},
            "guests": [],
        },
        "NO": {
            "players": {"declined@example.com": {"name": "Declined"}},
            "guests": [],
        },
        "MAYBE": {"players": {}, "guests": []},
    }
    mocker.patch("reminder_checker.handler.get_roster", return_value=roster)
    mock_confirm = mocker.patch("reminder_checker.handler.send_confirmation")

    result = handler({}, None)

    assert result["body"]["action"] == "game_confirmed"
    assert result["body"]["confirmedCount"] == 7
    assert mock_confirm.call_count == 7

    # Each confirmed player should receive the full roster
    for c in mock_confirm.call_args_list:
        assert c[0][2] == roster  # roster is the third argument


@pytest.mark.unit
def test_no_open_game(mocker):
    """Verify early return if no open game."""
    mocker.patch("reminder_checker.handler.get_current_open_game", return_value=None)
    mock_roster = mocker.patch("reminder_checker.handler.get_roster")
    mock_reminder = mocker.patch("reminder_checker.handler.send_reminder")

    result = handler({}, None)

    assert result["statusCode"] == 200
    assert result["body"] == "No open game"
    mock_roster.assert_not_called()
    mock_reminder.assert_not_called()


@pytest.mark.unit
def test_count_confirmed_with_guests():
    """Verify _count_confirmed counts players and their guests."""
    roster = {
        "YES": {
            "players": {
                "alice@example.com": {"name": "Alice"},
                "bob@example.com": {"name": "Bob"},
                "charlie@example.com": {"name": "Charlie"},
            },
            "guests": [
                {"name": "Mike", "sponsorName": "Alice"},
                {"name": "Sarah", "sponsorName": "Alice"},
                {"name": "Dave", "sponsorName": "Charlie"},
            ],
        },
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    }
    assert _count_confirmed(roster) == 6  # 3 players + 3 guests
