from datetime import date

import pytest

from game_finalizer.handler import handler


@pytest.mark.unit
def test_open_game_marked_played(mocker):
    """OPEN game on today's date should be marked PLAYED."""
    mocker.patch(
        "game_finalizer.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 28)},  # Saturday
    )
    mocker.patch(
        "game_finalizer.handler.get_game_status",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mock_update = mocker.patch("game_finalizer.handler.update_game_status")
    mocker.patch(
        "game_finalizer.handler.get_roster",
        return_value={
            "YES": {"players": {}, "guests": []},
            "NO": {"players": {}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        },
    )

    result = handler({}, None)

    mock_update.assert_called_once_with("2026-03-28", "PLAYED")
    assert result["body"]["action"] == "game_marked_played"
    assert result["body"]["gameDate"] == "2026-03-28"


@pytest.mark.unit
def test_cancelled_game_no_op(mocker):
    """CANCELLED game should not be updated."""
    mocker.patch(
        "game_finalizer.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 28)},
    )
    mocker.patch(
        "game_finalizer.handler.get_game_status",
        return_value={"gameDate": "2026-03-28", "status": "CANCELLED"},
    )
    mock_update = mocker.patch("game_finalizer.handler.update_game_status")

    result = handler({}, None)

    mock_update.assert_not_called()
    assert result["body"]["action"] == "no_action"
    assert result["body"]["status"] == "CANCELLED"


@pytest.mark.unit
def test_played_game_no_op(mocker):
    """Already PLAYED game should not be updated again."""
    mocker.patch(
        "game_finalizer.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 28)},
    )
    mocker.patch(
        "game_finalizer.handler.get_game_status",
        return_value={"gameDate": "2026-03-28", "status": "PLAYED"},
    )
    mock_update = mocker.patch("game_finalizer.handler.update_game_status")

    result = handler({}, None)

    mock_update.assert_not_called()
    assert result["body"]["action"] == "no_action"
    assert result["body"]["status"] == "PLAYED"


@pytest.mark.unit
def test_no_game_found(mocker):
    """No game record for today should return early with no update."""
    mocker.patch(
        "game_finalizer.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 28)},
    )
    mocker.patch("game_finalizer.handler.get_game_status", return_value=None)
    mock_update = mocker.patch("game_finalizer.handler.update_game_status")

    result = handler({}, None)

    mock_update.assert_not_called()
    assert result["body"] == "No game found"


@pytest.mark.unit
def test_uses_today_as_game_date(mocker):
    """Handler must query DynamoDB using today's ISO date."""
    mocker.patch(
        "game_finalizer.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 28)},
    )
    mock_get = mocker.patch("game_finalizer.handler.get_game_status", return_value=None)
    mocker.patch("game_finalizer.handler.update_game_status")

    handler({}, None)

    mock_get.assert_called_once_with("2026-03-28")


@pytest.mark.unit
def test_game_finalizer_deletes_guest_entries(mocker):
    """game_finalizer deletes guest Players entries from YES, NO, and MAYBE."""
    game_date = "2026-04-05"
    yes_guests = [
        {"pk": "john@example.com", "sk": "guest#active", "name": "John",
         "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
    ]
    no_guests = [
        {"pk": "bob@example.com", "sk": "guest#active#Jane", "name": "Jane",
         "sponsorEmail": "bob@example.com", "sponsorName": "Bob"},
    ]

    mocker.patch(
        "game_finalizer.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 4, 5)},
    )
    mocker.patch(
        "game_finalizer.handler.get_game_status",
        return_value={"gameDate": game_date, "status": "OPEN"},
    )
    mock_update = mocker.patch("game_finalizer.handler.update_game_status")
    mocker.patch(
        "game_finalizer.handler.get_roster",
        return_value={
            "YES": {"players": {}, "guests": yes_guests},
            "NO": {"players": {}, "guests": no_guests},
            "MAYBE": {"players": {}, "guests": []},
        },
    )
    mock_delete = mocker.patch("game_finalizer.handler.delete_guest_entries")

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
def test_game_finalizer_no_guests_does_not_call_delete(mocker):
    """game_finalizer skips delete_guest_entries when no guests exist."""
    mocker.patch(
        "game_finalizer.handler.date",
        wraps=date,
        **{"today.return_value": date(2026, 3, 28)},
    )
    mocker.patch(
        "game_finalizer.handler.get_game_status",
        return_value={"gameDate": "2026-03-28", "status": "OPEN"},
    )
    mocker.patch("game_finalizer.handler.update_game_status")
    mocker.patch(
        "game_finalizer.handler.get_roster",
        return_value={
            "YES": {"players": {}, "guests": []},
            "NO": {"players": {}, "guests": []},
            "MAYBE": {"players": {}, "guests": []},
        },
    )
    mock_delete = mocker.patch("game_finalizer.handler.delete_guest_entries")

    handler({}, None)

    mock_delete.assert_not_called()
