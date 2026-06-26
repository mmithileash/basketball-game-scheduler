import pytest

from weekly_scheduler.handler import handler


def _make_event():
    return {}


@pytest.mark.unit
def test_prompt_sent_when_no_week_status(mocker):
    """When no weekStatus exists for next week, prompt is sent to all active admins."""
    mocker.patch("weekly_scheduler.handler.get_week_status", return_value=None)
    mocker.patch(
        "weekly_scheduler.handler.get_active_admins",
        return_value=[{"email": "admin@example.com", "name": "Admin"}],
    )
    mock_prompt = mocker.patch("weekly_scheduler.handler.send_admin_weekly_prompt")

    result = handler(_make_event(), None)

    assert result["statusCode"] == 200
    assert result["body"]["action"] == "prompt_sent"
    mock_prompt.assert_called_once()
    _, week_start = mock_prompt.call_args[0]
    assert week_start is not None


@pytest.mark.unit
def test_prompt_suppressed_when_game_count_at_max(mocker):
    """When week already has max games scheduled, no prompt is sent."""
    mocker.patch(
        "weekly_scheduler.handler.get_week_status",
        return_value={"gameCount": 1, "adminResponded": True},
    )
    mock_prompt = mocker.patch("weekly_scheduler.handler.send_admin_weekly_prompt")

    result = handler(_make_event(), None)

    assert result["body"]["action"] == "no_prompt"
    mock_prompt.assert_not_called()


@pytest.mark.unit
def test_prompt_suppressed_when_admin_already_responded(mocker):
    """When admin already responded (but gameCount < max), no prompt is sent."""
    mocker.patch(
        "weekly_scheduler.handler.get_week_status",
        return_value={"gameCount": 0, "adminResponded": True},
    )
    mock_prompt = mocker.patch("weekly_scheduler.handler.send_admin_weekly_prompt")

    result = handler(_make_event(), None)

    assert result["body"]["action"] == "no_prompt"
    mock_prompt.assert_not_called()


@pytest.mark.unit
def test_prompt_sent_to_multiple_admins(mocker):
    """Prompt is sent to every active admin."""
    mocker.patch("weekly_scheduler.handler.get_week_status", return_value=None)
    mocker.patch(
        "weekly_scheduler.handler.get_active_admins",
        return_value=[
            {"email": "admin1@example.com", "name": "Admin 1"},
            {"email": "admin2@example.com", "name": "Admin 2"},
        ],
    )
    mock_prompt = mocker.patch("weekly_scheduler.handler.send_admin_weekly_prompt")

    result = handler(_make_event(), None)

    assert mock_prompt.call_count == 2
    assert result["body"]["adminCount"] == 2


@pytest.mark.unit
def test_prompt_continues_if_one_send_fails(mocker):
    """A single failed email send does not abort prompting other admins."""
    mocker.patch("weekly_scheduler.handler.get_week_status", return_value=None)
    mocker.patch(
        "weekly_scheduler.handler.get_active_admins",
        return_value=[
            {"email": "admin1@example.com", "name": "Admin 1"},
            {"email": "admin2@example.com", "name": "Admin 2"},
        ],
    )
    mock_prompt = mocker.patch(
        "weekly_scheduler.handler.send_admin_weekly_prompt",
        side_effect=[Exception("SES error"), None],
    )

    result = handler(_make_event(), None)

    assert result["statusCode"] == 200
    assert mock_prompt.call_count == 2
