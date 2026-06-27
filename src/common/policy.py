"""Per-game policy: the rules that decide a game's timing and viability.

A policy is a single map carried on the game's gameStatus record:

    {
        "minPlayers": int,        # go/no-go floor
        "threshold": int,         # turnout count that switches tiers
        "longGame":  {"startTime": str, "durationHours": int},
        "shortGame": {"startTime": str, "durationHours": int},
    }

A "fixed" game is represented by making both tiers identical; there is no
separate non-tiered schema and no `tiered` flag. Consumers decide presentation
by comparing the two tiers for equality (see `is_fixed`).
"""
from typing import Any


def default_policy(config: Any) -> dict[str, Any]:
    """Seed a two-tier policy from configuration defaults."""
    return {
        "minPlayers": config.min_players,
        "threshold": config.long_game_threshold,
        "longGame": {
            "startTime": config.long_game_start_time,
            "durationHours": config.long_game_duration_hours,
        },
        "shortGame": {
            "startTime": config.short_game_start_time,
            "durationHours": config.short_game_duration_hours,
        },
    }


def fixed_policy(
    start_time: str,
    duration_hours: int,
    threshold: int,
    min_players: int,
) -> dict[str, Any]:
    """Seed a fixed policy: both tiers equal to the supplied time and duration."""
    tier = {"startTime": start_time, "durationHours": duration_hours}
    return {
        "minPlayers": min_players,
        "threshold": threshold,
        "longGame": dict(tier),
        "shortGame": dict(tier),
    }


def resolve_tier(policy: dict[str, Any], confirmed_count: int) -> dict[str, Any]:
    """Resolve a policy plus a confirmed count to the applicable tier.

    The long-game tier applies at or above the threshold, the short-game tier
    below it. This is the single source of truth for the rule, shared by the
    announcement and confirmation steps.
    """
    tier = policy["longGame"] if confirmed_count >= policy["threshold"] else policy["shortGame"]
    return {
        "startTime": tier["startTime"],
        "durationHours": int(tier["durationHours"]),
    }


def is_fixed(policy: dict[str, Any]) -> bool:
    """Return True when both tiers are identical (a fixed game)."""
    long_game = policy["longGame"]
    short_game = policy["shortGame"]
    return (
        long_game["startTime"] == short_game["startTime"]
        and int(long_game["durationHours"]) == int(short_game["durationHours"])
    )
