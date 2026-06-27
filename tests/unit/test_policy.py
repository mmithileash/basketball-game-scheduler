"""Tests for the shared game-policy module."""
import pytest

from common.policy import default_policy, fixed_policy, is_fixed, resolve_tier


class _Cfg:
    long_game_start_time = "10:00 AM"
    long_game_duration_hours = 2
    short_game_start_time = "11:00 AM"
    short_game_duration_hours = 1
    long_game_threshold = 10
    min_players = 6


@pytest.mark.unit
def test_default_policy_seeds_two_tiers_from_config():
    policy = default_policy(_Cfg())
    assert policy["minPlayers"] == 6
    assert policy["threshold"] == 10
    assert policy["longGame"] == {"startTime": "10:00 AM", "durationHours": 2}
    assert policy["shortGame"] == {"startTime": "11:00 AM", "durationHours": 1}


@pytest.mark.unit
def test_default_policy_is_not_fixed():
    assert is_fixed(default_policy(_Cfg())) is False


@pytest.mark.unit
def test_fixed_policy_has_equal_tiers():
    policy = fixed_policy("9:00 AM", 2, threshold=10, min_players=6)
    assert policy["longGame"] == policy["shortGame"] == {"startTime": "9:00 AM", "durationHours": 2}
    assert is_fixed(policy) is True


@pytest.mark.unit
def test_resolve_tier_picks_long_game_at_or_above_threshold():
    policy = default_policy(_Cfg())
    assert resolve_tier(policy, 10) == {"startTime": "10:00 AM", "durationHours": 2}
    assert resolve_tier(policy, 11) == {"startTime": "10:00 AM", "durationHours": 2}


@pytest.mark.unit
def test_resolve_tier_picks_short_game_below_threshold():
    policy = default_policy(_Cfg())
    assert resolve_tier(policy, 9) == {"startTime": "11:00 AM", "durationHours": 1}
