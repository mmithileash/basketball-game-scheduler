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
