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

1. **Monday 9AM UTC** — `weekly-scheduler` Lambda checks the `weekStatus` item for next week (7 days out); if fewer than `MAX_GAMES_PER_WEEK` games are scheduled, emails active admins asking whether to schedule game(s).
2. **Admin replies** in natural language (e.g. "Tuesday and Saturday", "No games this week") — `admin-processor` calls Bedrock to parse `SCHEDULE_GAMES` (one or more `{date, startTime?, durationHours?}`) or `NO_GAMES_THIS_WEEK`. Each game is classified into a policy: neither field → default two-tier policy from config; both → fixed policy (equal tiers); exactly one → ambiguous, which holds the **whole batch** (nothing scheduled) and emails the admin to resend. For each valid game it creates the DynamoDB record (with policy) and starts a Step Functions execution named `game-{gameDate}`.
3. **Tuesday 9PM UTC** — `weekly-cutoff-checker` Lambda: if the admin hasn't responded, marks the week `no_response` and emails all players that there's no game.
4. **Per-game Step Functions execution** (`basketball-game-lifecycle`) drives the lifecycle via 4 Lambda tasks, each gated by the game still being `OPEN`:
   - `announce_task` (T-7d) — tentative announcement rendered from the game's policy (two turnout branches when tiered, one line when fixed)
   - `reminder_task` (T-4d) — low-signup reminder if confirmed < the policy's `minPlayers`
   - `confirm_or_cancel_task` (T-2d) — go/no-go on the policy's `minPlayers`; resolves the tier from turnout and **freezes** `confirmedStartTime`/`confirmedDurationHours` onto the game record
   - `finalize_task` (game day) — marks `PLAYED`, deletes guest Players-table entries
5. **Players reply** to emails with natural language — SES Receipt Rules store raw emails in S3, triggering `email-processor` Lambda, which calls **AWS Bedrock (Claude Haiku)** to classify intent (JOIN/DECLINE/BRING_GUESTS/MAYBE/etc.), updates DynamoDB, and replies. When multiple games are open simultaneously, the reply is routed via a `[Game: YYYY-MM-DD]` subject marker, falling back to single-open-game inference or a clarification request.
6. **`CANCEL_GAME` admin command** stops the game's Step Functions execution in addition to notifying players.

### Lambda Functions (`src/`)

- `weekly_scheduler/handler.py` — Monday prompt to admins for next week's games
- `weekly_cutoff_checker/handler.py` — Tuesday cutoff; no-game fallback if admin didn't respond
- `game_lifecycle/announce_task.py`, `reminder_task.py`, `confirm_or_cancel_task.py`, `finalize_task.py` — Step Functions task Lambdas for the per-game lifecycle
- `email_processor/handler.py` — Parses S3-stored inbound email, calls Bedrock for NLU, updates roster, replies
- `admin_processor/handler.py` — Processes admin command emails (schedule/cancel games, add/deactivate/reactivate players)

### Shared Modules (`src/common/`)

- `config.py` — `Config` dataclass loaded from environment variables
- `date_utils.py` — `week_start_for_date()`, `sfn_timestamps_for_game()`
- `dynamo.py` — All DynamoDB operations (create game, get/update roster, get pending players, weekStatus helpers, get_open_games)
- `email_service.py` — SES send layer with templates for all email types
- `bedrock_client.py` — Bedrock integration; `parse_player_email` returns structured JSON `{intent, guests, confirmed_guest_names, query_target, reply_draft}`; `parse_admin_email` returns `{intent, game_date, email, name, is_admin, games}` where each game is `{date, startTime|null, durationHours|null}` (unmentioned timing is reported as null, never defaulted)
- `policy.py` — per-game policy helpers: `default_policy()`, `fixed_policy()`, `resolve_tier()` (shared by announce + confirm), `is_fixed()`

### DynamoDB Data Model

**Players table:** PK=`email`, SK=`active`

**Games table** (single-table): PK=`gameDate` (YYYY-MM-DD) or `weekStartDate` (Monday, YYYY-MM-DD), SK varies:
- `gameStatus` → `{status: OPEN|CANCELLED|PLAYED, createdAt, policy, confirmedStartTime?, confirmedDurationHours?}` — `policy` is `{minPlayers, threshold, longGame:{startTime,durationHours}, shortGame:{startTime,durationHours}}` (a fixed game has equal tiers); the `confirmed*` fields are frozen at the confirm step
- `playerStatus#YES` → map of `{email: {guests: [...]}}`
- `playerStatus#NO` → map of `{email: {}}`
- `playerStatus#MAYBE` → map of `{email: {}}`
- `weekStatus` (PK=Monday date) → `{gameCount, adminResponded, reason?: no_response|admin_declined}` — additive, incremented atomically by `create_game()`

### Infrastructure (`terraform/`)

Provisions: EventBridge cron rules, 8 Lambda functions, a Step Functions state machine (`basketball-game-lifecycle`), SES domain + receipt rules, S3 bucket (email storage), DynamoDB tables, Route 53 MX records, IAM roles.

Key variables (set in `terraform.tfvars`): `domain_name`, `sender_email`, `admin_email`, `game_location`, `bedrock_model_id`, `min_players`, `long_game_threshold`, `long_game_start_time`, `long_game_duration_hours`, `short_game_start_time`, `short_game_duration_hours`, `max_games_per_week`. The threshold and tier start/duration values seed each game's policy at creation; they are not read at runtime.

### Testing

- **Unit tests** (`tests/unit/`) — Use `moto` to mock AWS services; no external dependencies
- **Integration tests** (`tests/integration/`) — Use LocalStack via Docker Compose; test full end-to-end flows

#Python preferences
- Python version: 3.12.13 (managed by pyenv via `.python-version`)
- Use f-strings when possible



