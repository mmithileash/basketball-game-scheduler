import pytest

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


def _patch_s3(mocker, sender: str, subject: str, body: str):
    return mocker.patch(
        "admin_processor.handler.fetch_email_from_s3",
        return_value=(sender, subject, body),
    )


@pytest.mark.unit
def test_non_admin_sender_is_rejected(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=False)
    mock_send = mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "notadmin@example.com", "Cancel", "Cancel game")

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
        "games": [],
    })
    mocker.patch("admin_processor.handler.get_game_status", return_value=None)
    mock_pre_cancel = mocker.patch("admin_processor.handler.pre_cancel_game")
    mock_send = mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Cancel", "Cancel game on April 11")

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_pre_cancel.assert_called_once_with("2026-04-11")
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
    _patch_s3(mocker, "admin@example.com", "Cancel", "Cancel game on April 11")

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_update.assert_called_once_with("2026-04-11", "CANCELLED")
    assert mock_send.call_count == 3
    mock_send.assert_any_call("alice@example.com", "2026-04-11", include_unsubscribe=True)
    mock_send.assert_any_call("bob@example.com", "2026-04-11", include_unsubscribe=True)
    mock_send.assert_any_call("charlie@example.com", "2026-04-11", include_unsubscribe=True)
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
    _patch_s3(mocker, "admin@example.com", "Cancel", "Cancel the game")

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
    _patch_s3(mocker, "admin@example.com", "Add", "Add player newplayer@example.com New Player")

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
    _patch_s3(mocker, "admin@example.com", "Add Admin", "Add admin newadmin@example.com New Admin")

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
    _patch_s3(mocker, "admin@example.com", "Remove", "Deactivate alice@example.com")

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
    _patch_s3(mocker, "admin@example.com", "Reactivate", "Reactivate alice@example.com")

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
    _patch_s3(mocker, "admin@example.com", "??", "blahrgh")

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    mock_send.assert_called_once()
    assert "understand" in mock_send.call_args[0][2].lower()


@pytest.mark.unit
def test_cancel_game_notifies_guests_with_contact_email(mocker):
    """Cancelling an OPEN game also emails guests with their own contact email."""
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.get_game_status",
                 return_value={"status": "OPEN"})
    mocker.patch("admin_processor.handler.update_game_status")
    mocker.patch("admin_processor.handler.get_roster", return_value={
        "YES": {
            "players": {"alice@example.com": {"name": "Alice"}},
            "guests": [
                {"pk": "john@example.com", "sk": "guest#active", "name": "John",
                 "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
                {"pk": "alice@example.com", "sk": "guest#active#Jane", "name": "Jane",
                 "sponsorEmail": "alice@example.com", "sponsorName": "Alice"},
            ],
        },
        "MAYBE": {"players": {}, "guests": []},
    })
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-04-19",
        "email": None,
        "name": None,
        "is_admin": None,
    })
    mock_broadcast = mocker.patch("admin_processor.handler.send_admin_cancelled_broadcast")
    mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Cancel", "Cancel game 2026-04-19")

    result = handler(_make_s3_event("test-email-bucket", "admin/somefile"), None)

    assert result["statusCode"] == 200
    broadcast_recipients = {call[0][0] for call in mock_broadcast.call_args_list}
    # alice (player) and john (guest with contact email) should be notified
    assert "alice@example.com" in broadcast_recipients
    assert "john@example.com" in broadcast_recipients
    # Jane has no own email (sk=guest#active#Jane), should NOT get a direct email
    assert broadcast_recipients == {"alice@example.com", "john@example.com"}


@pytest.mark.unit
def test_cancel_game_broadcast_includes_unsubscribe_for_players_not_guests(mocker):
    """Players notified of admin cancellation receive the unsubscribe footer; guests do not."""
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-04-19",
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [],
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
    _patch_s3(mocker, "admin@example.com", "Cancel", "Cancel 2026-04-19")

    handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    # Player call must include include_unsubscribe=True
    player_calls = [c for c in mock_broadcast.call_args_list if c[0][0] == "alice@example.com"]
    assert len(player_calls) == 1
    assert player_calls[0].kwargs.get("include_unsubscribe") is True

    # Guest call must NOT set include_unsubscribe=True
    guest_calls = [c for c in mock_broadcast.call_args_list if c[0][0] == "john@example.com"]
    assert len(guest_calls) == 1
    assert not guest_calls[0].kwargs.get("include_unsubscribe", False)


# ---------------------------------------------------------------------------
# SCHEDULE_GAMES
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_schedule_games_unspecified_creates_default_two_tier_policy(mocker):
    """A game with neither time nor duration seeds a default (non-fixed) policy."""
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "SCHEDULE_GAMES",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [
            {"date": "2026-07-07", "startTime": None, "durationHours": None},
            {"date": "2026-07-10", "startTime": None, "durationHours": None},
        ],
    })
    mock_create = mocker.patch("admin_processor.handler.create_game")
    mock_sfn = mocker.MagicMock()
    mocker.patch("admin_processor.handler._get_sfn_client", return_value=mock_sfn)
    mock_send = mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Re: Schedule", "Tuesday and Thursday")

    result = handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    assert result["statusCode"] == 200
    assert mock_create.call_count == 2
    dates = {call[0][0] for call in mock_create.call_args_list}
    assert dates == {"2026-07-07", "2026-07-10"}
    # Each game seeded with a two-tier (non-fixed) policy
    from common.policy import is_fixed
    for call in mock_create.call_args_list:
        policy = call[0][1]
        assert is_fixed(policy) is False
        assert policy["longGame"] != policy["shortGame"]
    assert mock_sfn.start_execution.call_count == 2
    mock_send.assert_called_once()


@pytest.mark.unit
def test_schedule_games_fully_specified_creates_fixed_policy(mocker):
    """A game with both time and duration seeds a fixed policy (equal tiers)."""
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "SCHEDULE_GAMES",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [
            {"date": "2026-07-11", "startTime": "9:00 AM", "durationHours": 2},
        ],
    })
    mock_create = mocker.patch("admin_processor.handler.create_game")
    mock_sfn = mocker.MagicMock()
    mocker.patch("admin_processor.handler._get_sfn_client", return_value=mock_sfn)
    mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Re: Schedule", "Saturday, 2 hours from 9am")

    result = handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    assert result["statusCode"] == 200
    mock_create.assert_called_once()
    game_date, policy = mock_create.call_args[0]
    assert game_date == "2026-07-11"
    from common.policy import is_fixed
    assert is_fixed(policy) is True
    assert policy["longGame"] == {"startTime": "9:00 AM", "durationHours": 2}
    assert policy["shortGame"] == {"startTime": "9:00 AM", "durationHours": 2}
    assert mock_sfn.start_execution.call_count == 1


@pytest.mark.unit
def test_schedule_games_partial_holds_whole_batch(mocker):
    """A partial game (only one of time/duration) holds the entire batch."""
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "SCHEDULE_GAMES",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [
            {"date": "2026-07-07", "startTime": None, "durationHours": None},
            {"date": "2026-07-11", "startTime": "9:00 AM", "durationHours": None},
        ],
    })
    mock_create = mocker.patch("admin_processor.handler.create_game")
    mock_sfn = mocker.MagicMock()
    mocker.patch("admin_processor.handler._get_sfn_client", return_value=mock_sfn)
    mock_send = mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Re: Schedule", "Tuesday, and Saturday from 9am")

    result = handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    assert result["statusCode"] == 200
    # No games created, no lifecycle executions started
    mock_create.assert_not_called()
    mock_sfn.start_execution.assert_not_called()
    # A single clarification email is sent, naming the partial game and missing field
    mock_send.assert_called_once()
    body = mock_send.call_args[0][2]
    assert "2026-07-11" in body
    assert "duration" in body.lower()


@pytest.mark.unit
def test_schedule_games_sfn_already_exists_is_noop(mocker):
    """ExecutionAlreadyExists during start_execution is silently ignored."""
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "SCHEDULE_GAMES",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [{"date": "2026-07-07", "startTime": None, "durationHours": None}],
    })
    mocker.patch("admin_processor.handler.create_game")
    mock_sfn = mocker.MagicMock()
    mock_sfn.start_execution.side_effect = mock_sfn.exceptions.ExecutionAlreadyExists(
        {"Error": {"Code": "ExecutionAlreadyExists", "Message": "already exists"}}, "StartExecution"
    )
    mocker.patch("admin_processor.handler._get_sfn_client", return_value=mock_sfn)
    mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Re: Schedule", "Tuesday")

    result = handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    assert result["statusCode"] == 200


@pytest.mark.unit
def test_schedule_games_empty_parse_sends_error(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "SCHEDULE_GAMES",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [],
    })
    mock_send = mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Re: Schedule", "umm maybe")

    result = handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    assert result["statusCode"] == 200
    assert "parse" in mock_send.call_args[0][2].lower() or "date" in mock_send.call_args[0][2].lower()


# ---------------------------------------------------------------------------
# NO_GAMES_THIS_WEEK
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_games_this_week_notifies_all_players(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "NO_GAMES_THIS_WEEK",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [],
    })
    mock_set_no_game = mocker.patch("admin_processor.handler.set_week_no_game")
    mocker.patch(
        "admin_processor.handler.get_active_players",
        return_value=[
            {"email": "alice@example.com", "name": "Alice"},
            {"email": "bob@example.com", "name": "Bob"},
        ],
    )
    mock_notify = mocker.patch("admin_processor.handler.send_no_game_this_week")
    mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Re:", "No games this week")

    result = handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    assert result["statusCode"] == 200
    mock_set_no_game.assert_called_once()
    _, reason = mock_set_no_game.call_args[0]
    assert reason == "admin_declined"
    assert mock_notify.call_count == 2


# ---------------------------------------------------------------------------
# CANCEL_GAME + SFN stop (Slice 5)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_cancel_game_stops_sfn_execution(mocker):
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-07-07",
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [],
    })
    mocker.patch("admin_processor.handler.get_game_status", return_value={"status": "OPEN"})
    mocker.patch("admin_processor.handler.update_game_status")
    mocker.patch("admin_processor.handler.get_roster", return_value={
        "YES": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    })
    mock_sfn = mocker.MagicMock()
    mocker.patch("admin_processor.handler._get_sfn_client", return_value=mock_sfn)
    mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Cancel", "Cancel 2026-07-07")

    handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    mock_sfn.stop_execution.assert_called_once()
    call_kwargs = mock_sfn.stop_execution.call_args[1]
    assert "game-2026-07-07" in call_kwargs["executionArn"]


@pytest.mark.unit
def test_cancel_game_sfn_not_found_is_ignored(mocker):
    """ExecutionDoesNotExist from stop_execution is silently ignored."""
    mocker.patch("admin_processor.handler.is_admin", return_value=True)
    mocker.patch("admin_processor.handler.parse_admin_email", return_value={
        "intent": "CANCEL_GAME",
        "game_date": "2026-07-07",
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [],
    })
    mocker.patch("admin_processor.handler.get_game_status", return_value={"status": "OPEN"})
    mocker.patch("admin_processor.handler.update_game_status")
    mocker.patch("admin_processor.handler.get_roster", return_value={
        "YES": {"players": {}, "guests": []},
        "MAYBE": {"players": {}, "guests": []},
    })
    mock_sfn = mocker.MagicMock()
    mock_sfn.stop_execution.side_effect = Exception("ExecutionDoesNotExist: does not exist")
    mocker.patch("admin_processor.handler._get_sfn_client", return_value=mock_sfn)
    mocker.patch("admin_processor.handler.send_email")
    _patch_s3(mocker, "admin@example.com", "Cancel", "Cancel 2026-07-07")

    result = handler(_make_s3_event("test-email-bucket", "admin/x"), None)

    assert result["statusCode"] == 200
