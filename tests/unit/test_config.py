"""Tests for Config policy seeds."""
import dataclasses

import pytest

from common.config import load_config


@pytest.mark.unit
def test_config_exposes_tier_seeds(monkeypatch):
    monkeypatch.setenv("LONG_GAME_START_TIME", "9:00 AM")
    monkeypatch.setenv("LONG_GAME_DURATION_HOURS", "2")
    monkeypatch.setenv("SHORT_GAME_START_TIME", "12:00 PM")
    monkeypatch.setenv("SHORT_GAME_DURATION_HOURS", "1")

    config = load_config()

    assert config.long_game_start_time == "9:00 AM"
    assert config.long_game_duration_hours == 2
    assert config.short_game_start_time == "12:00 PM"
    assert config.short_game_duration_hours == 1


@pytest.mark.unit
def test_config_tier_seeds_have_defaults():
    config = load_config()
    assert config.long_game_start_time == "10:00 AM"
    assert config.long_game_duration_hours == 2
    assert config.short_game_start_time == "11:00 AM"
    assert config.short_game_duration_hours == 1


@pytest.mark.unit
def test_config_no_longer_has_game_time():
    fields = {f.name for f in dataclasses.fields(load_config())}
    assert "game_time" not in fields
