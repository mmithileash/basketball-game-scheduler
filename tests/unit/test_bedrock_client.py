import io
import json

import pytest
from moto import mock_aws

from common.bedrock_client import parse_player_email, parse_admin_email


def _make_bedrock_response(intent, guests=None, confirmed_guest_names=None, query_target=None,
                           reply_draft="Got it!"):
    """Build a mock Bedrock invoke_model response."""
    result = {
        "intent": intent,
        "guests": guests or [],
        "confirmed_guest_names": confirmed_guest_names or [],
        "query_target": query_target,
        "reply_draft": reply_draft,
    }
    response_body = {
        "content": [{"text": json.dumps(result)}],
    }
    body_bytes = json.dumps(response_body).encode("utf-8")
    return {"body": io.BytesIO(body_bytes)}


@pytest.fixture
def empty_roster():
    return {"YES": {}, "NO": {}, "MAYBE": {}}


@pytest.mark.unit
def test_parse_join_intent(mocker, empty_roster):
    """Mock Bedrock response for 'I'm in!', verify intent=JOIN."""
    mock_client = mocker.MagicMock()
    mock_client.invoke_model.return_value = _make_bedrock_response("JOIN")
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email("I'm in!", "player@example.com", empty_roster)

    assert result["intent"] == "JOIN"
    mock_client.invoke_model.assert_called_once()


@pytest.mark.unit
def test_parse_decline_intent(mocker, empty_roster):
    """Mock 'Can't make it', verify intent=DECLINE."""
    mock_client = mocker.MagicMock()
    mock_client.invoke_model.return_value = _make_bedrock_response("DECLINE")
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email("Can't make it", "player@example.com", empty_roster)

    assert result["intent"] == "DECLINE"


@pytest.mark.unit
def test_parse_bring_guests(mocker, empty_roster):
    """Mock 'I'll bring 2 friends Mike and Sarah', verify BRING_GUESTS with guests."""
    mock_client = mocker.MagicMock()
    mock_client.invoke_model.return_value = _make_bedrock_response(
        "BRING_GUESTS", guests=[{"name": "Mike", "contact_email": None}, {"name": "Sarah", "contact_email": None}]
    )
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email(
        "I'll bring 2 friends Mike and Sarah", "player@example.com", empty_roster
    )

    assert result["intent"] == "BRING_GUESTS"
    assert result["guests"] == [{"name": "Mike", "contact_email": None}, {"name": "Sarah", "contact_email": None}]


@pytest.mark.unit
def test_parse_query_roster(mocker, empty_roster):
    """Mock 'Who's playing?', verify intent=QUERY_ROSTER."""
    mock_client = mocker.MagicMock()
    mock_client.invoke_model.return_value = _make_bedrock_response("QUERY_ROSTER")
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email("Who's playing?", "player@example.com", empty_roster)

    assert result["intent"] == "QUERY_ROSTER"


@pytest.mark.unit
def test_parse_query_player(mocker, empty_roster):
    """Mock 'Is John coming?', verify intent=QUERY_PLAYER."""
    mock_client = mocker.MagicMock()
    mock_client.invoke_model.return_value = _make_bedrock_response(
        "QUERY_PLAYER", query_target="john@example.com"
    )
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email("Is John coming?", "player@example.com", empty_roster)

    assert result["intent"] == "QUERY_PLAYER"
    assert result["query_target"] == "john@example.com"


@pytest.mark.unit
def test_parse_error_fallback(mocker, empty_roster):
    """Mock Bedrock error, verify graceful fallback."""
    mock_client = mocker.MagicMock()
    mock_client.invoke_model.side_effect = Exception("Bedrock is down")
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email("I'm in!", "player@example.com", empty_roster)

    assert result["intent"] == "MAYBE"
    assert result["guests"] == []
    assert result["confirmed_guest_names"] == []
    assert "trouble" in result["reply_draft"].lower()


@pytest.mark.unit
def test_parse_json_decode_error_fallback(mocker, empty_roster):
    """Mock Bedrock returning invalid JSON, verify fallback."""
    mock_client = mocker.MagicMock()
    response_body = {"content": [{"text": "not valid json"}]}
    body_bytes = json.dumps(response_body).encode("utf-8")
    mock_client.invoke_model.return_value = {"body": io.BytesIO(body_bytes)}
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email("I'm in!", "player@example.com", empty_roster)

    assert result["intent"] == "MAYBE"
    assert "trouble" in result["reply_draft"].lower()


@pytest.mark.unit
def test_parse_player_email_bring_guests_new_schema(mocker, empty_roster):
    """BRING_GUESTS returns guests as list of {name, contact_email} objects."""
    mock_client = mocker.MagicMock()
    mock_client.invoke_model.return_value = _make_bedrock_response(
        "BRING_GUESTS",
        guests=[
            {"name": "John", "contact_email": "john@example.com"},
            {"name": "Jane", "contact_email": None},
        ],
    )
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email(
        "I'm in, bringing John (john@example.com) and Jane", "alice@example.com", empty_roster
    )

    assert result["intent"] == "BRING_GUESTS"
    assert len(result["guests"]) == 2
    assert result["guests"][0] == {"name": "John", "contact_email": "john@example.com"}
    assert result["guests"][1] == {"name": "Jane", "contact_email": None}
    assert result["confirmed_guest_names"] == []


@pytest.mark.unit
def test_parse_player_email_guest_confirm(mocker, empty_roster):
    """GUEST_CONFIRM returns confirmed_guest_names."""
    mock_client = mocker.MagicMock()
    mock_client.invoke_model.return_value = _make_bedrock_response(
        "GUEST_CONFIRM",
        confirmed_guest_names=["John"],
    )
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email("John is still coming", "alice@example.com", empty_roster)

    assert result["intent"] == "GUEST_CONFIRM"
    assert result["confirmed_guest_names"] == ["John"]
    assert result["guests"] == []


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
    assert result["email"] is None
    assert result["name"] is None
    assert result["is_admin"] is None


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
    assert result["game_date"] is None


@pytest.mark.unit
def test_parse_admin_email_json_error_returns_unknown(mocker):
    mock_response = {
        "body": mocker.MagicMock(read=lambda: b'{"content": [{"text": "not json"}]}')
    }
    mocker.patch("common.bedrock_client._get_bedrock_client").return_value.invoke_model.return_value = mock_response

    result = parse_admin_email("gibberish", "admin@example.com")

    assert result["intent"] == "UNKNOWN"
    assert result["game_date"] is None
    assert result["email"] is None
    assert result["name"] is None
    assert result["is_admin"] is None


@pytest.mark.unit
def test_parse_admin_email_exception_returns_unknown(mocker):
    mocker.patch(
        "common.bedrock_client._get_bedrock_client"
    ).return_value.invoke_model.side_effect = Exception("Bedrock unavailable")

    result = parse_admin_email("Cancel the game", "admin@example.com")

    assert result["intent"] == "UNKNOWN"
    assert result["game_date"] is None
    assert result["email"] is None
    assert result["name"] is None
    assert result["is_admin"] is None


def _make_admin_bedrock_response(mocker, payload: dict):
    mock_response = {
        "body": mocker.MagicMock(
            read=lambda: json.dumps({"content": [{"text": json.dumps(payload)}]}).encode()
        )
    }
    mocker.patch(
        "common.bedrock_client._get_bedrock_client"
    ).return_value.invoke_model.return_value = mock_response


@pytest.mark.unit
def test_parse_admin_email_schedule_games(mocker):
    _make_admin_bedrock_response(mocker, {
        "intent": "SCHEDULE_GAMES",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [
            {"date": "2026-07-07", "startTime": None, "durationHours": None},
            {"date": "2026-07-10", "startTime": "9:00 AM", "durationHours": 2},
        ],
    })

    result = parse_admin_email("Schedule Tuesday, and Friday 2 hours from 9am", "admin@example.com")

    assert result["intent"] == "SCHEDULE_GAMES"
    assert len(result["games"]) == 2
    assert result["games"][0]["date"] == "2026-07-07"
    # Unmentioned timing is reported as null, not defaulted
    assert result["games"][0]["startTime"] is None
    assert result["games"][0]["durationHours"] is None
    assert result["games"][1]["startTime"] == "9:00 AM"
    assert result["games"][1]["durationHours"] == 2


@pytest.mark.unit
def test_parse_admin_email_schedule_games_reports_unmentioned_timing_as_null(mocker):
    """A bare date with no timing keys comes back with startTime/durationHours None."""
    _make_admin_bedrock_response(mocker, {
        "intent": "SCHEDULE_GAMES",
        "games": [{"date": "2026-07-07"}],
    })

    result = parse_admin_email("Schedule Tuesday", "admin@example.com")

    assert result["games"][0] == {"date": "2026-07-07", "startTime": None, "durationHours": None}


@pytest.mark.unit
def test_parse_admin_email_no_games_this_week(mocker):
    _make_admin_bedrock_response(mocker, {
        "intent": "NO_GAMES_THIS_WEEK",
        "game_date": None,
        "email": None,
        "name": None,
        "is_admin": None,
        "games": [],
    })

    result = parse_admin_email("No games this week", "admin@example.com")

    assert result["intent"] == "NO_GAMES_THIS_WEEK"
    assert result["games"] == []


@pytest.mark.unit
def test_parse_admin_email_cancel_game_includes_games_field(mocker):
    """CANCEL_GAME response includes games=[] (backward compat)."""
    _make_admin_bedrock_response(mocker, {
        "intent": "CANCEL_GAME",
        "game_date": "2026-07-07",
        "email": None,
        "name": None,
        "is_admin": None,
    })

    result = parse_admin_email("Cancel the game on July 7", "admin@example.com")

    assert result["intent"] == "CANCEL_GAME"
    assert result["games"] == []
