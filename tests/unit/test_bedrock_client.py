import io
import json

import pytest
from moto import mock_aws

from common.bedrock_client import parse_player_email


def _make_bedrock_response(intent, guest_count=0, guest_names=None, query_target=None,
                           reply_draft="Got it!"):
    """Build a mock Bedrock invoke_model response."""
    result = {
        "intent": intent,
        "guest_count": guest_count,
        "guest_names": guest_names or [],
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
        "BRING_GUESTS", guest_count=2, guest_names=["Mike", "Sarah"]
    )
    mocker.patch("common.bedrock_client._get_bedrock_client", return_value=mock_client)

    result = parse_player_email(
        "I'll bring 2 friends Mike and Sarah", "player@example.com", empty_roster
    )

    assert result["intent"] == "BRING_GUESTS"
    assert result["guest_count"] == 2
    assert result["guest_names"] == ["Mike", "Sarah"]


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
    assert result["guest_count"] == 0
    assert result["guest_names"] == []
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
