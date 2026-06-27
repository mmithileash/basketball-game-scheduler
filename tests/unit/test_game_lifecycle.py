"""Tests for the four SFN game lifecycle task Lambdas."""
import boto3
import pytest
from moto import mock_aws

from game_lifecycle.announce_task import handler as announce_handler
from game_lifecycle.confirm_or_cancel_task import handler as confirm_or_cancel_handler
from game_lifecycle.finalize_task import handler as finalize_handler
from game_lifecycle.reminder_task import handler as reminder_handler


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _create_tables():
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
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
    dynamodb.create_table(
        TableName="test-games",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return dynamodb


def _reset_caches():
    import common.dynamo as d
    d._config = None
    d._dynamodb = None
    d._client = None
    import common.email_service as e
    e._config = None
    e._ses_client = None


def _seed_open_game(dynamodb, game_date: str):
    from common.dynamo import create_game
    create_game(game_date)


def _seed_players(dynamodb, players: list[dict]):
    table = dynamodb.Table("test-players")
    for p in players:
        table.put_item(Item=p)


# ---------------------------------------------------------------------------
# announce_task
# ---------------------------------------------------------------------------

_TIERED_POLICY = {
    "minPlayers": 6,
    "threshold": 10,
    "longGame": {"startTime": "10:00 AM", "durationHours": 2},
    "shortGame": {"startTime": "11:00 AM", "durationHours": 1},
}

_FIXED_POLICY = {
    "minPlayers": 6,
    "threshold": 10,
    "longGame": {"startTime": "9:00 AM", "durationHours": 2},
    "shortGame": {"startTime": "9:00 AM", "durationHours": 2},
}


@pytest.mark.unit
def test_announce_task_sends_to_all_active_players(mocker):
    mocker.patch(
        "game_lifecycle.announce_task.get_game_status",
        return_value={"gameDate": "2026-07-07", "status": "OPEN", "policy": _TIERED_POLICY},
    )
    mocker.patch(
        "game_lifecycle.announce_task.get_active_players",
        return_value=[
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
        ],
    )
    mock_announce = mocker.patch("game_lifecycle.announce_task.send_tentative_announcement")

    result = announce_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is True
    assert mock_announce.call_count == 2
    emails = {call[0][0] for call in mock_announce.call_args_list}
    assert emails == {"alice@example.com", "bob@example.com"}
    # The game's policy is passed through to the email layer
    assert mock_announce.call_args_list[0][0][3] == _TIERED_POLICY


@pytest.mark.unit
def test_announce_task_tiered_game_shows_two_branches(mocker):
    mocker.patch(
        "game_lifecycle.announce_task.get_game_status",
        return_value={"status": "OPEN", "policy": _TIERED_POLICY},
    )
    mocker.patch(
        "game_lifecycle.announce_task.get_active_players",
        return_value=[{"email": "alice@example.com", "name": "Alice"}],
    )
    mock_send = mocker.patch("common.email_service.send_email")

    announce_handler({"game_date": "2026-07-07"}, None)

    body = mock_send.call_args[0][2]
    assert "10:00 AM" in body
    assert "11:00 AM" in body
    assert "10+" in body  # threshold branch stated
    assert "at least 6" in body


@pytest.mark.unit
def test_announce_task_fixed_game_shows_single_line(mocker):
    mocker.patch(
        "game_lifecycle.announce_task.get_game_status",
        return_value={"status": "OPEN", "policy": _FIXED_POLICY},
    )
    mocker.patch(
        "game_lifecycle.announce_task.get_active_players",
        return_value=[{"email": "alice@example.com", "name": "Alice"}],
    )
    mock_send = mocker.patch("common.email_service.send_email")

    announce_handler({"game_date": "2026-07-07"}, None)

    body = mock_send.call_args[0][2]
    assert "9:00 AM" in body
    # No conditional/two-branch language for a fixed game
    assert "10+" not in body
    assert "depend" not in body.lower()


@pytest.mark.unit
def test_announce_task_skips_when_game_cancelled(mocker):
    mocker.patch(
        "game_lifecycle.announce_task.get_game_status",
        return_value={"gameDate": "2026-07-07", "status": "CANCELLED"},
    )
    mock_announce = mocker.patch("game_lifecycle.announce_task.send_tentative_announcement")

    result = announce_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is False
    mock_announce.assert_not_called()


@pytest.mark.unit
def test_announce_task_skips_when_game_missing(mocker):
    mocker.patch("game_lifecycle.announce_task.get_game_status", return_value=None)
    mock_announce = mocker.patch("game_lifecycle.announce_task.send_tentative_announcement")

    result = announce_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is False
    mock_announce.assert_not_called()


@pytest.mark.unit
def test_announce_task_continues_if_one_send_fails(mocker):
    mocker.patch(
        "game_lifecycle.announce_task.get_game_status",
        return_value={"status": "OPEN", "policy": _TIERED_POLICY},
    )
    mocker.patch(
        "game_lifecycle.announce_task.get_active_players",
        return_value=[
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
        ],
    )
    mock_announce = mocker.patch(
        "game_lifecycle.announce_task.send_tentative_announcement",
        side_effect=[Exception("SES down"), None],
    )

    result = announce_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is True
    assert mock_announce.call_count == 2


# ---------------------------------------------------------------------------
# reminder_task
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_reminder_task_sends_when_below_policy_minimum(mocker):
    mocker.patch(
        "game_lifecycle.reminder_task.get_game_status",
        return_value={"status": "OPEN", "policy": _TIERED_POLICY},
    )
    mocker.patch(
        "game_lifecycle.reminder_task.get_roster",
        return_value={"YES": {"players": {}, "guests": []}, "NO": {"players": {}, "guests": []}, "MAYBE": {"players": {}, "guests": []}},
    )
    mocker.patch(
        "game_lifecycle.reminder_task.get_pending_players",
        return_value=[{"email": "alice@example.com", "name": "Alice"}],
    )
    mock_reminder = mocker.patch("game_lifecycle.reminder_task.send_reminder")

    result = reminder_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is True
    mock_reminder.assert_called_once()
    # min_players from the policy is passed through to the email
    assert mock_reminder.call_args[0][4] == 6


@pytest.mark.unit
def test_reminder_task_skips_when_policy_minimum_met(mocker):
    mocker.patch(
        "game_lifecycle.reminder_task.get_game_status",
        return_value={"status": "OPEN", "policy": _TIERED_POLICY},
    )
    # 6 players confirmed (policy minPlayers=6)
    players = {f"p{i}@example.com": {"name": f"P{i}"} for i in range(6)}
    mocker.patch(
        "game_lifecycle.reminder_task.get_roster",
        return_value={"YES": {"players": players, "guests": []}, "NO": {"players": {}, "guests": []}, "MAYBE": {"players": {}, "guests": []}},
    )
    mock_reminder = mocker.patch("game_lifecycle.reminder_task.send_reminder")

    result = reminder_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is True
    mock_reminder.assert_not_called()


@pytest.mark.unit
def test_reminder_task_skips_when_game_not_open(mocker):
    mocker.patch(
        "game_lifecycle.reminder_task.get_game_status",
        return_value={"status": "CANCELLED"},
    )
    mock_reminder = mocker.patch("game_lifecycle.reminder_task.send_reminder")

    result = reminder_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is False
    mock_reminder.assert_not_called()


# ---------------------------------------------------------------------------
# confirm_or_cancel_task
# ---------------------------------------------------------------------------

def _roster_with_yes(n: int) -> dict:
    players = {f"p{i}@e.com": {"name": f"P{i}"} for i in range(n)}
    return {
        "YES": {"players": players, "guests": []},
        "NO": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    }


@pytest.mark.unit
def test_confirm_or_cancel_cancels_when_below_policy_minimum(mocker):
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_game_status",
        return_value={"status": "OPEN", "policy": _TIERED_POLICY},
    )
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_roster",
        return_value=_roster_with_yes(1),
    )
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_pending_players",
        return_value=[{"email": "b@e.com", "name": "B"}],
    )
    mock_update = mocker.patch("game_lifecycle.confirm_or_cancel_task.update_game_status")
    mock_cancel = mocker.patch("game_lifecycle.confirm_or_cancel_task.send_cancellation")
    mock_freeze = mocker.patch("game_lifecycle.confirm_or_cancel_task.freeze_game_schedule")

    result = confirm_or_cancel_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is False
    mock_update.assert_called_once_with("2026-07-07", "CANCELLED")
    assert mock_cancel.call_count >= 1
    mock_freeze.assert_not_called()


@pytest.mark.unit
def test_confirm_or_cancel_freezes_short_tier_below_threshold(mocker):
    """6-9 confirmed → short tier (11:00 AM, 1 hour), frozen onto the record."""
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_game_status",
        return_value={"status": "OPEN", "policy": _TIERED_POLICY},
    )
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_roster",
        return_value=_roster_with_yes(6),
    )
    mocker.patch("game_lifecycle.confirm_or_cancel_task.get_pending_players", return_value=[])
    mocker.patch("game_lifecycle.confirm_or_cancel_task.update_game_status")
    mock_freeze = mocker.patch("game_lifecycle.confirm_or_cancel_task.freeze_game_schedule")
    mock_confirm = mocker.patch("game_lifecycle.confirm_or_cancel_task.send_final_confirmation_with_duration")

    result = confirm_or_cancel_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is True
    mock_freeze.assert_called_once_with("2026-07-07", "11:00 AM", 1)
    assert mock_confirm.call_count == 6
    _, _, _, start_time, duration = mock_confirm.call_args[0]
    assert start_time == "11:00 AM"
    assert duration == 1


@pytest.mark.unit
def test_confirm_or_cancel_freezes_long_tier_at_or_above_threshold(mocker):
    """10+ confirmed → long tier (10:00 AM, 2 hours), frozen onto the record."""
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_game_status",
        return_value={"status": "OPEN", "policy": _TIERED_POLICY},
    )
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_roster",
        return_value=_roster_with_yes(10),
    )
    mocker.patch("game_lifecycle.confirm_or_cancel_task.get_pending_players", return_value=[])
    mocker.patch("game_lifecycle.confirm_or_cancel_task.update_game_status")
    mock_freeze = mocker.patch("game_lifecycle.confirm_or_cancel_task.freeze_game_schedule")
    mock_confirm = mocker.patch("game_lifecycle.confirm_or_cancel_task.send_final_confirmation_with_duration")

    result = confirm_or_cancel_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is True
    mock_freeze.assert_called_once_with("2026-07-07", "10:00 AM", 2)
    _, _, _, start_time, duration = mock_confirm.call_args[0]
    assert start_time == "10:00 AM"
    assert duration == 2


@pytest.mark.unit
def test_confirm_or_cancel_go_no_go_uses_policy_minimum_not_config(mocker):
    """Go/no-go gates on the policy's minPlayers, not the global config default (6)."""
    policy = {
        "minPlayers": 5,
        "threshold": 10,
        "longGame": {"startTime": "10:00 AM", "durationHours": 2},
        "shortGame": {"startTime": "11:00 AM", "durationHours": 1},
    }
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_game_status",
        return_value={"status": "OPEN", "policy": policy},
    )
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_roster",
        return_value=_roster_with_yes(5),
    )
    mocker.patch("game_lifecycle.confirm_or_cancel_task.get_pending_players", return_value=[])
    mocker.patch("game_lifecycle.confirm_or_cancel_task.update_game_status")
    mocker.patch("game_lifecycle.confirm_or_cancel_task.freeze_game_schedule")
    mock_confirm = mocker.patch("game_lifecycle.confirm_or_cancel_task.send_final_confirmation_with_duration")
    mock_cancel = mocker.patch("game_lifecycle.confirm_or_cancel_task.send_cancellation")

    result = confirm_or_cancel_handler({"game_date": "2026-07-07"}, None)

    # 5 confirmed meets policy min of 5 → game is confirmed, not cancelled
    assert result["game_open"] is True
    mock_cancel.assert_not_called()
    assert mock_confirm.call_count == 5


@pytest.mark.unit
def test_confirm_or_cancel_skips_when_game_already_cancelled(mocker):
    mocker.patch(
        "game_lifecycle.confirm_or_cancel_task.get_game_status",
        return_value={"status": "CANCELLED"},
    )
    mock_update = mocker.patch("game_lifecycle.confirm_or_cancel_task.update_game_status")

    result = confirm_or_cancel_handler({"game_date": "2026-07-07"}, None)

    assert result["game_open"] is False
    mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# finalize_task
# ---------------------------------------------------------------------------

@pytest.mark.unit
@mock_aws
def test_finalize_task_marks_game_played_and_deletes_guests():
    _reset_caches()
    dynamodb = _create_tables()

    from common.dynamo import add_guests_to_game_status, create_game, get_game_status

    create_game("2026-07-07")
    guest = {
        "pk": "john@example.com",
        "sk": "guest#active",
        "name": "John",
        "sponsorEmail": "alice@example.com",
        "sponsorName": "Alice",
    }
    add_guests_to_game_status("2026-07-07", "YES", [guest])
    dynamodb.Table("test-players").put_item(
        Item={"email": "john@example.com", "active": "guest#active", "name": "John", "sponsorEmail": "alice@example.com", "gameDate": "2026-07-07"}
    )

    result = finalize_handler({"game_date": "2026-07-07"}, None)

    assert result["action"] == "marked_played"
    assert result["guests_deleted"] == 1

    status = get_game_status("2026-07-07")
    assert status["status"] == "PLAYED"

    guest_item = dynamodb.Table("test-players").get_item(
        Key={"email": "john@example.com", "active": "guest#active"}
    ).get("Item")
    assert guest_item is None


@pytest.mark.unit
def test_finalize_task_noop_when_cancelled(mocker):
    mocker.patch(
        "game_lifecycle.finalize_task.get_game_status",
        return_value={"status": "CANCELLED"},
    )
    mock_update = mocker.patch("game_lifecycle.finalize_task.update_game_status")

    result = finalize_handler({"game_date": "2026-07-07"}, None)

    assert result["action"] == "no_op"
    mock_update.assert_not_called()


@pytest.mark.unit
def test_finalize_task_noop_when_missing(mocker):
    mocker.patch("game_lifecycle.finalize_task.get_game_status", return_value=None)
    mock_update = mocker.patch("game_lifecycle.finalize_task.update_game_status")

    result = finalize_handler({"game_date": "2026-07-07"}, None)

    assert result["action"] == "no_op"
    mock_update.assert_not_called()
