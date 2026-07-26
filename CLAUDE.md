# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
make install

# Run unit tests (no Docker required, uses moto for AWS mocking)
make test-unit

# Run integration tests (requires Docker + LocalStack)
make test-integration

# Run all tests
make test-all

# Run a single test file
pytest tests/unit/test_dynamo.py -v

# Run a single test
pytest tests/unit/test_dynamo.py::test_function_name -v

# Package Lambda functions (zips each function + common/ into .build/)
make package

# Import players from CSV into DynamoDB
make import-players

# Terraform
make tf-init && make tf-plan && make tf-apply

# Clean build artifacts
make clean
```

Linting is not yet configured (`make lint` is a TODO placeholder).

## Architecture

Fully serverless email-based basketball game coordination system on AWS (eu-west-1). The entire player interaction is email-only — no web UI.

### Core Flow

Admin-driven, multi-game-per-week scheduling, orchestrated by Step Functions per game:

1. **Monday 9AM UTC** — `weekly-scheduler` Lambda targets the **current** week (the week containing today); if the week has fewer than `MIN_GAMES_PER_WEEK` **live** games (a floor, computed from the week's non-cancelled game rows via `count_games_in_week`) **and** no no-game decision is recorded, it emails active admins asking whether to schedule game(s).
2. **Admin replies** in natural language (e.g. "Tuesday and Saturday", "No games this week") — `admin-processor` calls Bedrock to parse `SCHEDULE_GAMES` (one or more `{date, startTime?, durationHours?}`) or `NO_GAMES_THIS_WEEK`. Each game is classified into a policy: neither field → default two-tier policy from config; both → fixed policy (equal tiers); exactly one → ambiguous. The handler computes one shared `now`, then any problem — an ambiguous spec, an unparseable admin start time, or a start **less than 48h from now** (the viability guard) — **holds the whole batch** (nothing scheduled) and emails the admin to resend. For each valid game it computes the adaptive lifecycle timestamps, creates the DynamoDB record (with policy and the floored `confirmAt` cutoff), and starts a Step Functions execution named `game-{gameDate}` seeded with those timestamps. Scheduling a future-week date is allowed — the game and its week are tracked on that date's own week.
3. **Tuesday 9PM UTC** — `weekly-cutoff-checker` Lambda targets the **current** week: if a no-game decision is already recorded it no-ops (idempotent); if the week has ≥1 live game it no-ops (games self-announce); otherwise it marks the week `no_response` and emails all players that there's no game.
4. **Per-game Step Functions execution** (`basketball-game-lifecycle`) drives the lifecycle via 4 Lambda tasks, each gated by the game still being `OPEN`. The four wait times are **adaptive**, anchored to the game's real (earliest-tier) start and scaling between a compressed 48h/36h/24h floor (same-week games) and a 7d/4d/2d cap (far-out games); they are computed once at creation by `sfn_timestamps_for_game(game_date, policy, now)` and frozen into the execution input (no structural state-machine change):
   - `announce_task` (announce_at: 48h→7d before start; fires at creation for same-week games) — tentative announcement rendered from the game's policy, showing the game's floored RSVP cutoff when known (two turnout branches when tiered, one line when fixed)
   - `reminder_task` (reminder_at: 36h→4d before start) — low-signup reminder if confirmed < the policy's `minPlayers`
   - `confirm_or_cancel_task` (confirm_at: 24h→2d before start, floored **down** to the hour) — go/no-go on the policy's `minPlayers`; resolves the tier from turnout and **freezes** `confirmedStartTime`/`confirmedDurationHours` onto the game record
   - `finalize_task` (finalize_at: game day, the conservative maximum end across both tiers so evening/past-midnight games aren't closed early) — marks `PLAYED`, deletes guest Players-table entries
5. **Players reply** to emails with natural language — SES Receipt Rules store raw emails in S3, triggering `email-processor` Lambda, which calls **AWS Bedrock (Claude Haiku)** to classify intent (JOIN/DECLINE/BRING_GUESTS/MAYBE/etc.), updates DynamoDB, and replies. When multiple games are open simultaneously, the reply is routed via a `[Game: YYYY-MM-DD]` subject marker, falling back to single-open-game inference or a clarification request.
6. **`CANCEL_GAME` admin command** stops the game's Step Functions execution in addition to notifying players.

### Lambda Functions (`src/`)

- `weekly_scheduler/handler.py` — Monday prompt to admins for the current week's games
- `weekly_cutoff_checker/handler.py` — Tuesday cutoff; no-game fallback if admin didn't respond
- `game_lifecycle/announce_task.py`, `reminder_task.py`, `confirm_or_cancel_task.py`, `finalize_task.py` — Step Functions task Lambdas for the per-game lifecycle
- `email_processor/handler.py` — Parses S3-stored inbound email, calls Bedrock for NLU, updates roster, replies
- `admin_processor/handler.py` — Processes admin command emails (schedule/cancel games, add/deactivate/reactivate players)

### Shared Modules (`src/common/`)

- `config.py` — `Config` dataclass loaded from environment variables
- `date_utils.py` — `week_start_for_date()`; `parse_start_time()` (precise hour+minute parse of display strings, raises on unparseable); `game_start()` (a game's earliest-tier start, shared by the guard and the lifecycle); adaptive `sfn_timestamps_for_game(game_date, policy, now)`
- `dynamo.py` — All DynamoDB operations (create game with floored `confirmAt` snapshot, get/update roster, get pending players, `count_games_in_week` + no-game-decision week helpers, get_open_games)
- `email_service.py` — SES send layer with templates for all email types
- `bedrock_client.py` — Bedrock integration; `parse_player_email` returns structured JSON `{intent, guests, confirmed_guest_names, query_target, reply_draft}`; `parse_admin_email` returns `{intent, game_date, email, name, is_admin, games}` where each game is `{date, startTime|null, durationHours|null, location|null, mapUrl|null}` (unmentioned timing and venue are reported as null, never defaulted)
- `policy.py` — per-game policy helpers: `default_policy()`, `fixed_policy()`, `resolve_tier()` (shared by announce + confirm), `is_fixed()`

### DynamoDB Data Model

**Players table:** PK=`email`, SK=`active`

**Games table** (single-table): PK attribute is `pk`, an **entity-prefixed** value, SK varies:
- game rows: `pk = GAME#<ISO date>` (e.g. `GAME#2026-06-27`)
- week-status row: `pk = WEEK#<ISO Monday>` (e.g. `WEEK#2026-06-29`)

The prefix (`GAME#`/`WEEK#`) is an **internal storage detail confined to `common/dynamo.py`** — built via the `game_pk()`/`week_pk()` helpers and stripped on read by `strip_pk()`. Every other layer (handlers, Step Functions input, the `game-{date}` execution name, email templates) deals in bare ISO dates, and read functions (`get_game_status`, `get_open_games`) still expose a bare `gameDate` field. The prefix exists because the old `gameDate` PK attribute lied: the `weekStatus` row is keyed by a Monday week-start, not a game date, which repeatedly misled readers into thinking the wrong date was stored. **The week's game count is not stored** — it is computed live from the week's dated game rows (`count_games_in_week`, excluding `CANCELLED`), which is authoritative and correctly reflects cancellations. The `weekStatus` row persists **only a no-game decision**, the one fact not derivable from game rows.

SK values:
- `gameStatus` → `{status: OPEN|CANCELLED|PLAYED, createdAt, policy, location, confirmAt?, confirmedStartTime?, confirmedDurationHours?}` — `policy` is `{minPlayers, threshold, longGame:{startTime,durationHours}, shortGame:{startTime,durationHours}}` (a fixed game has equal tiers); `location` is `{name, mapUrl}` snapshotted at creation from config default unless the admin overrides it (both name and map URL required); `confirmAt` is the floored confirmation cutoff snapshotted at creation as the single authoritative display source (read by the announce task and the inbound-email guest follow-up); the `confirmed*` fields are frozen at the confirm step
- `playerStatus#YES` → map of `{email: {guests: [...]}}`
- `playerStatus#NO` → map of `{email: {}}`
- `playerStatus#MAYBE` → map of `{email: {}}`
- `weekStatus` (on the `WEEK#<Monday>` partition) → `{reason: no_response|admin_declined, createdAt, modifiedAt}` — written only to record a no-game decision (keeps the Tuesday cutoff idempotent); no counter, no `adminResponded` flag

### Infrastructure (`terraform/`)

Provisions: EventBridge cron rules, 8 Lambda functions, a Step Functions state machine (`basketball-game-lifecycle`), SES domain + receipt rules, S3 bucket (email storage), DynamoDB tables, Route 53 MX records, IAM roles.

Key variables (set in `terraform.tfvars`): `domain_name`, `sender_email`, `admin_email`, `default_game_location`, `default_game_map_url`, `bedrock_model_id`, `min_players`, `long_game_threshold`, `long_game_start_time`, `long_game_duration_hours`, `short_game_start_time`, `short_game_duration_hours`, `min_games_per_week`. `min_games_per_week` is a **floor** (target), not a cap — the Monday prompt fires while the current week has fewer than this many live games; it never limits how many games can be scheduled. The threshold and tier start/duration values seed each game's policy at creation; they are not read at runtime.

### Testing

- **Unit tests** (`tests/unit/`) — Use `moto` to mock AWS services; no external dependencies
- **Integration tests** (`tests/integration/`) — Use LocalStack via Docker Compose; test full end-to-end flows

#Python preferences
- Python version: 3.12.13 (managed by pyenv via `.python-version`)
- Use f-strings when possible



