# PRD: Per-Game Location & Map

## Problem Statement

Every game announcement and confirmation email shows a single, system-wide venue: the location name and map link come from the `DEFAULT_GAME_LOCATION` / `DEFAULT_GAME_MAP_URL` environment variables baked into the Lambda config. If a particular week's game is played somewhere other than the usual court, the admin has no way to say so — the email will always name the default venue, and there is no per-game record of where a game was actually played.

The admin already schedules games in natural language and can attach a start time and duration to any individual game ("Saturday at 10am for 2 hours"). Location is the one game-shaping detail they cannot express.

## Solution

Let the admin optionally name a venue (and its map link) per game, in the same natural-language scheduling email they already send:

> "Tuesday at the usual court, Saturday at the YMCA — map https://maps.app/xyz"

Each game stores its own location. When the admin says nothing about location, the game inherits the configured default, exactly as today — so existing behavior is unchanged for anyone who never mentions a venue. When the admin does specify a venue, that game's announcement and confirmation emails show the specified name and map link instead.

A venue override requires **both** a name and a valid map URL. A half-specified override (name but no link, or a link that isn't a real URL) is treated the same way a half-specified time already is: the whole batch is held, nothing is scheduled, and the admin is asked to resend a complete command.

## User Stories

1. As an admin, I want to schedule a game without mentioning a location, so that it uses our usual configured venue with no extra effort.
2. As an admin, I want to name a different venue for a specific game, so that players are told the correct place when we play somewhere other than the usual court.
3. As an admin, I want to attach a map link to a custom venue, so that players can navigate to an unfamiliar location.
4. As an admin, I want to give different venues to different games in one scheduling email, so that a mixed-venue week (e.g. Tuesday at the usual court, Saturday at the YMCA) is handled in a single message.
5. As an admin, I want a game I schedule with no venue details to keep the curated default name and map pin, so that the map link still points precisely where it always has.
6. As an admin, if I name a venue but forget the map link, I want to be told my command was incomplete and nothing was scheduled, so that I never accidentally announce a venue with no way to find it.
7. As an admin, if I name a venue but the map link isn't a real URL, I want that treated as a missing link, so that players never receive a broken map hyperlink.
8. As an admin, I want an incomplete location to hold the entire batch (not just the one game), so that partial scheduling is consistent with how a partial start-time/duration is already handled.
9. As a player, I want the game announcement email to show where the game is, so that I know where to go.
10. As a player, I want the venue in the email to be a clickable map link when one is available, so that I can navigate there directly.
11. As a player, I want the final confirmation email to show the same venue that was announced, so that the location is consistent across the game's emails.
12. As a player, I want a game announced at a custom venue to name that venue rather than the default, so that I am not misled about where to play.
13. As an operator, I want each game record to carry the venue it was scheduled with, so that changing the configured default later never rewrites the venue of games that already exist.
14. As an operator, I want games created before this feature to keep rendering the configured default venue, so that no data backfill or migration is required.
15. As an operator, I want the existing `DEFAULT_GAME_LOCATION` / `DEFAULT_GAME_MAP_URL` configuration to keep working as the default source, so that deployment configuration does not have to change.

## Implementation Decisions

**Data model (Games table, `gameStatus` item).** A new top-level field `location: {name, mapUrl}` is added to the `gameStatus` item, sitting parallel to `policy` — *not* nested inside `policy`. Location is a venue, conceptually distinct from the turnout/timing tiers, and it does not participate in tier resolution or the confirm-step freeze. It is written once at creation and never mutated over the lifecycle.

**Snapshot at creation.** Location is snapshotted onto the record at game creation, the same way `policy` is. A default (non-overridden) game stores `{name: config.default_game_location, mapUrl: config.default_game_map_url}` — the record is always self-describing, and a later change to the configured default never retroactively alters past games.

**Parse contract (`common/bedrock_client.parse_admin_email`).** Each game object in the returned `games[]` array gains two new nullable fields, `location` (the venue name) and `mapUrl`, alongside the existing `startTime` / `durationHours`. They follow the same contract: an unmentioned field is reported as `null`, never defaulted. The system prompt and its examples are extended so the model can extract a venue name and an accompanying map URL from prose.

**Classification (`admin_processor` handler).** In the same per-game loop that already classifies timing, location is classified with the same both-or-neither rule:
- both `location` and `mapUrl` null → default (no override; pass `location=None` to game creation)
- both present, and `mapUrl` begins with `http://` or `https://` → override `{name, mapUrl}`
- exactly one present, or a name accompanied by a `mapUrl` that fails the `http(s)` check → **partial**: appended to the existing `partials` list, which holds the whole batch (nothing scheduled) and emails the admin to resend a complete command.

The configured default is **exempt** from the both-or-neither rule: `DEFAULT_GAME_MAP_URL` may legitimately be empty, in which case the email renders a bare venue name (unchanged from today).

**Write path (`common/dynamo.create_game`).** Signature becomes `create_game(game_date, policy=None, location=None)`. `create_game` owns the default snapshot: when `location` is `None` it seeds `{name: config.default_game_location, mapUrl: config.default_game_map_url}`. This keeps the many bare `create_game(date)` call sites working and mirrors how `policy=None` is defaulted today.

**Read path (`common/email_service`).** `_location_display()` is changed to accept the stored `location` dict rather than reaching into config directly; when the dict is absent or empty it falls back to `_get_config()` exactly as today (this fallback now only fires for games created before this feature). The two email functions that render a venue — `send_tentative_announcement` and `send_final_confirmation_with_duration` — gain a `location` parameter. Their callers, the `announce_task` and `confirm_or_cancel_task` Step Functions Lambdas, already load the game record and pass `game.get("location")` down. Reminder and finalize emails render no venue and are untouched.

**No new admin intent.** Location is settable only at scheduling time (the existing `SCHEDULE_GAMES` path). There is no command to change a scheduled game's venue and therefore no re-announcement of already-notified players.

## Testing Decisions

Tests assert externally observable behavior — what is written to the game record and what appears in outgoing emails — never internal representation. Unit tests use `moto` for AWS mocking, consistent with the existing suite; Bedrock parsing is exercised against the structured JSON contract, not the model itself.

Modules and behaviors to cover:

- **`create_game` (test_dynamo.py):** a default game snapshots the configured `{name, mapUrl}`; an explicit override is stored verbatim. Prior art: the existing `create_game` policy-default vs explicit-policy tests.
- **`parse_admin_email` (bedrock parsing tests):** games array carries `location`/`mapUrl` when the admin mentions them and reports `null` when they don't. Prior art: existing assertions that `startTime`/`durationHours` are `null` when unmentioned.
- **`admin_processor` (handler tests):** both-null → default game created; both-present-with-valid-URL → override stored; name-without-URL, URL-without-name, and name-with-non-URL → batch held with a resend email and nothing scheduled. Prior art: the existing partial start-time/duration "batch held" tests.
- **`_location_display` / email rendering (email_service tests):** an override renders its name and map link; an absent/empty stored location falls back to the configured default; an empty map URL renders a bare name. Prior art: existing announcement/confirmation email body assertions.

## Out of Scope

- Any command or flow to change a game's location after it has been scheduled (and the associated re-notification of players).
- Rendering a venue in reminder or finalize emails (they show none today).
- A named-venue registry or lookup, auto-generation of map URLs from a venue name, and geocoding/validation of the venue beyond the light `http(s)` URL-shape check.
- Backfilling location onto games created before this feature (the read-time config fallback covers them).
- Changes to the `DEFAULT_GAME_LOCATION` / `DEFAULT_GAME_MAP_URL` environment variables or Terraform configuration; they remain as the default source.

## Further Notes

This feature is a deliberate parallel to the existing per-game `policy`: config-seeded at creation, snapshotted onto the `gameStatus` item, and threaded to the email templates through the Step Functions task Lambdas that already load the game record. Reusing that pattern keeps the change purely additive — a parse field, a classification branch, a stored field, and a template parameter — with the highest-value seam being the `admin_processor` classification loop, where the both-or-neither validation already lives for timing.
