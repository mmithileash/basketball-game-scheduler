# PRD: Admin-Driven Multi-Game Scheduling Redesign

## Problem Statement

The current system schedules exactly one game every Saturday via a hardcoded Monday cron job — no admin input required or possible. This creates three compounding problems:

1. **No flexibility over game days.** Games can only happen on Saturdays. If the organiser wants a midweek game or needs to skip a week, the only option is to cancel after the fact, which sends a confusing cancellation notice to players.

2. **No per-week agency.** The system creates a game whether or not the organiser wants one. Weeks with holidays, low expected turnout, or organiser absence silently produce games that get cancelled late, wasting player attention.

3. **One game per week is hardwired.** There is no mechanism to schedule a second game in the same week even if demand exists. The data model and flow both assume exactly one open game at a time, so `email_processor` cannot route replies when two games overlap.

## Solution

Replace the automatic Saturday game creation with an **admin-prompted, Step-Functions-orchestrated flow**:

- Every Monday at 9 AM UTC the system emails the admin asking whether to schedule game(s) for the following week (Mon–Sun, 7 days out). Only prompts if fewer than the configured maximum are already scheduled for that week.
- The admin replies in natural language ("Tuesday and Saturday", "No games this week"). Bedrock parses the reply into structured game dates.
- For each confirmed game date, `admin_processor` creates the game record and starts a dedicated Step Functions execution named `game-{gameDate}`. The state machine drives the full lifecycle: announcement → reminder → confirm/cancel → finalize.
- If the admin hasn't replied by Tuesday 9 PM UTC, a cutoff checker treats the week as "no game" and notifies all players.
- Games can be on any day of the week, and multiple games per week are supported.
- Reply disambiguation uses a `[Game: YYYY-MM-DD]` subject-line marker so `email_processor` routes replies to the right game even when several are open simultaneously.

## User Stories

1. As an admin, I want to receive a weekly email prompt on Monday asking whether to schedule games for next week, so that I have explicit control over which weeks have games.
2. As an admin, I want to reply with natural language like "Tuesday and Saturday" to schedule multiple games at once, so that I don't need to send separate commands per game.
3. As an admin, I want the system to accept a time in my reply ("Thursday 7 PM"), so that games are not locked to a single default time.
4. As an admin, I want the system to default to 11:00 AM UTC if I don't specify a time, so that I don't have to include a time for standard games.
5. As an admin, I want to reply "No games this week" to explicitly decline scheduling, so that players are notified immediately rather than waiting until Tuesday's cutoff.
6. As an admin, I want to receive a confirmation email listing the dates I just scheduled, so that I can verify the system parsed my reply correctly.
7. As a player, I want to receive a game announcement 7 days before each game, so that I have enough lead time to plan.
8. As a player, I want the announcement to state a tentative game duration (1 hour normally, 2 hours if enough players confirm), so that I can block the right amount of time in my calendar.
9. As a player, I want to receive a reminder 4 days before the game if confirmed numbers are still low, so that I have another chance to RSVP before the deadline.
10. As a player, I want the go/no-go decision to happen 2 days before the game (not 1 day), so that I have more time to adjust plans if the game is cancelled.
11. As a player, I want the final confirmation email to include the locked-in duration (1 hr or 2 hr), so that I know exactly how long to stay.
12. As a player, I want to receive a "no game this week" notice when the admin declines to schedule or doesn't respond by Tuesday, so that I'm not left wondering.
13. As a player, I want to reply to a specific game's email and have the system know which game I'm RSVPing for (via the subject line), so that my reply is routed correctly when two games are open.
14. As a player, I want the system to ask me to clarify which game I mean if my reply doesn't reference a specific date and multiple games are open, so that my RSVP isn't silently misrouted.
15. As an admin, I want to cancel a game and have the associated Step Functions execution stopped immediately, so that no further emails (reminders, confirmations) are sent after cancellation.
16. As an admin, I want to schedule a game on any day of the week (not just Saturday), so that I can accommodate venue availability and player preferences.
17. As an admin, I want the system to allow up to N games per week (configurable), so that the Monday prompt is suppressed when the week is already full.
18. As a player, I want the announcement email subject to include a game-date marker (`[Game: YYYY-MM-DD]`), so that my email client's threading correctly groups replies to the right game.
19. As an admin, I want short-notice games (scheduled less than 7 days out) to work without special handling, so that I can call an ad-hoc game when needed.
20. As an admin, I want the `CANCEL_GAME` command to work the same as before for player notifications, with the addition of stopping the SFN execution, so that I don't need to learn a new cancellation flow.

## Implementation Decisions

### Weekly scheduling flow

- A new Lambda `weekly_scheduler` is triggered by an EventBridge cron at Monday 9 AM UTC. It reads a `weekStatus` item (keyed by the Monday date of the target week, 7 days out) to check how many games are already scheduled. If `gameCount < max_games_per_week`, it emails all active admins a prompt.
- A new Lambda `weekly_cutoff_checker` is triggered by an EventBridge cron at Tuesday 9 PM UTC. It reads the same `weekStatus` item. If `adminResponded` is still `false`, it writes `adminResponded=true, reason="no_response"` and notifies all players that there are no games this week.

### weekStatus DynamoDB item

- Stored in the existing Games table (no new table). PK = `weekStartDate` (YYYY-MM-DD, always the Monday), SK = `"weekStatus"`.
- Attributes: `gameCount` (Number), `adminResponded` (Boolean), `reason` (String, optional — `"no_response"` or `"admin_declined"`).
- `create_game()` atomically increments `gameCount` and sets `adminResponded = true` on the weekStatus item in the same `transact_write_items` call as the game creation — no separate write.
- `get_week_status(week_start_date)` is a single `get_item` — no scan.

### Bedrock intent additions

- `parse_admin_email` gains two new intents:
  - `SCHEDULE_GAMES`: extracts a list of `{date: "YYYY-MM-DD", time: "HH:MM UTC"}` entries. Time defaults to `"11:00 UTC"` in the system prompt if not mentioned.
  - `NO_GAMES_THIS_WEEK`: admin explicitly declines. No extra fields.
- The existing `CANCEL_GAME` intent's `game_date` is now any calendar date (not limited to Saturdays). The system prompt drops the "must be a Saturday" constraint and the `next_saturday()` reference.
- The return shape of `parse_admin_email` gains a `games` field (list, empty for non-SCHEDULE_GAMES intents).

### Per-game Step Functions state machine

- State machine name: `basketball-game-lifecycle`. Execution names are `game-{gameDate}` (deterministic — `StartExecution` with an existing name returns `ExecutionAlreadyExists`, treated as no-op).
- Input to each execution: `{game_date, announce_at, reminder_at, confirm_at, finalize_at}` — all timestamps computed at scheduling time from `game_date` (announce = gameDate−7d 9AM UTC, reminder = gameDate−4d 9AM UTC, confirm = gameDate−2d 9AM UTC, finalize = gameDate 1PM UTC).
- A Wait-until-past-timestamp resolves immediately, so short-notice games skip earlier stages naturally.
- Four Lambda task states, each separated by a Wait state and a Choice state:
  - `AnnounceGame` → reads `gameStatus`; if not OPEN, returns `game_open=false`; otherwise sends announcement to all active players, returns `game_open=true`.
  - `SendReminder` → reads `gameStatus`; if not OPEN, no-op; if `confirmed_count < MIN_PLAYERS`, sends reminder to pending players.
  - `ConfirmOrCancel` → reads `gameStatus`; if not OPEN, no-op; if `confirmed_count < MIN_PLAYERS` → cancels + notifies all; else confirms + locks duration (1 hr if confirmed < `LONG_GAME_THRESHOLD`, 2 hr otherwise) + notifies YES players.
  - `FinalizeGame` → reads `gameStatus`; if OPEN → marks PLAYED + deletes guest Players-table entries.
- After each Task state a Choice state checks `task_result.game_open`. If `false`, transitions to a terminal `Done` (Succeed) state. This guards against a race between `CANCEL_GAME` and a scheduled send — both layers (SFN stop + in-task status check) are intentional.

### Configuration additions

- `LONG_GAME_THRESHOLD` env var → `Config.long_game_threshold: int`, default `10`. Controls the confirmed-player threshold for 2-hour vs 1-hour game duration.
- `MAX_GAMES_PER_WEEK` env var → `Config.max_games_per_week: int`, default `1`. Controls how many games must be scheduled before the Monday prompt is suppressed.

### CANCEL_GAME admin command changes

- After marking the game CANCELLED and notifying players (existing behaviour), `admin_processor` calls `sfn.stop_execution(executionArn=...)`.
- The execution ARN is derived from the `GAME_LIFECYCLE_SFN_ARN` env var (state machine ARN) by replacing `:stateMachine:` with `:execution:` and appending `:game-{game_date}`.
- `ExecutionDoesNotExist` is silently ignored (game was scheduled before SFN existed, or execution already finished).

### email_processor multi-game disambiguation

- `get_upcoming_game()` (Saturday-locked) is replaced with `get_open_games()` — a scan of the Games table for all items where `sk = "gameStatus"` and `status = "OPEN"`.
- Game date resolution order: (a) parse `[Game: YYYY-MM-DD]` from the email's Subject header — primary; (b) if not found and exactly one open game exists, use that; (c) if multiple open games and no subject marker, call Bedrock and check `query_target` for a parseable YYYY-MM-DD; (d) if still ambiguous, reply asking the player to clarify which date they mean.

### Email subject marker

- All outbound game emails (announcement, reminder, confirmation, cancellation) use the subject format `Basketball Game - {game_date} [Game: {game_date}]`. Player replies inherit this subject via email threading, making step (a) above reliable.

### Retired components

- `announcement_sender` Lambda and its EventBridge schedule are removed. Its Monday game-creation responsibility moves to `admin_processor` (SCHEDULE_GAMES); its announcement-send responsibility moves to `game_lifecycle.announce_task`.
- `reminder_checker` Lambda and its Wed/Fri cron schedule are removed. Logic moves to `game_lifecycle.reminder_task` and `game_lifecycle.confirm_or_cancel_task`.
- `game_finalizer` Lambda and its Saturday cron schedule are removed. Logic moves to `game_lifecycle.finalize_task`.
- `next_saturday()` in `date_utils.py` is removed once all callers are updated.
- `get_current_open_game()` and `get_upcoming_game()` in `dynamo.py` are removed; callers move to `get_open_games()`.

## Testing Decisions

**What makes a good test here:** tests call the Lambda `handler(event, context)` function directly and assert on DynamoDB state (via moto) and SES/SFN calls (via moto or mocks). Tests do not reach into private helpers or assert on intermediate DynamoDB writes — only final observable state (emails sent, game status, weekStatus). The SFN state machine ASL is not unit-tested; only the individual task Lambda handlers are.

**Prior art:** `tests/unit/test_admin_processor.py`, `tests/unit/test_email_processor_handler.py`, `tests/unit/test_reminder_handler.py` — all follow the same pattern: mock AWS clients with `@mock_aws` (moto), set env vars via conftest autouse fixture, call `handler(event, context)`, assert on mocked client call counts and DynamoDB items.

**Modules with new or expanded tests:**

| Module | Test file | Notes |
|--------|-----------|-------|
| `common.dynamo` | `test_dynamo.py` | `get_week_status`, `set_week_no_game`, `create_game` (now atomically updates weekStatus), `get_open_games` |
| `common.bedrock_client` | `test_bedrock_client.py` | `SCHEDULE_GAMES` and `NO_GAMES_THIS_WEEK` intent parsing |
| `common.email_service` | `test_email_service.py` | `send_admin_weekly_prompt`, `send_no_game_this_week`, `send_tentative_announcement`, `send_final_confirmation_with_duration` |
| `weekly_scheduler` | `test_weekly_scheduler.py` | new — prompt sent when gameCount < max; suppressed when at max; suppressed when adminResponded already |
| `weekly_cutoff_checker` | `test_weekly_cutoff_checker.py` | new — no-game sent if no admin response; no-op if already responded |
| `game_lifecycle.announce_task` | `test_game_lifecycle.py` | new — sends to all active players when OPEN; skips when CANCELLED |
| `game_lifecycle.reminder_task` | `test_game_lifecycle.py` | new — sends reminder when confirmed < MIN_PLAYERS; skips when OPEN but count met |
| `game_lifecycle.confirm_or_cancel_task` | `test_game_lifecycle.py` | new — cancels when under threshold; confirms with correct duration when at/above both thresholds |
| `game_lifecycle.finalize_task` | `test_game_lifecycle.py` | new — marks PLAYED + deletes guests when OPEN; no-op when CANCELLED |
| `admin_processor` | `test_admin_processor.py` | extended — SCHEDULE_GAMES creates game + starts SFN; CANCEL_GAME stops SFN execution |
| `email_processor` | `test_email_processor_handler.py` | extended — subject marker routes to correct game; multiple open games with no marker triggers clarification reply |

## Out of Scope

- **Migration of in-flight games.** Any game already OPEN under the old system when this code ships is not automatically migrated into a SFN execution. Those games will continue to exist in DynamoDB but will not receive SFN-driven reminder/confirmation emails.
- **Mid-week re-prompting.** If a game is cancelled after it was created (dropping `gameCount` below `max_games_per_week`), the system does not re-trigger the Monday prompt mid-week.
- **SFN state machine integration tests.** The state machine's Wait/Choice wiring is not covered by unit tests. Verification is via Terraform validation and manual staging deployment.
- **Email HTML templates.** All emails remain plain-text. No HTML rendering.
- **Ad-hoc game creation via admin command.** The `SCHEDULE_GAMES` intent also handles direct admin emails outside the Monday prompt flow (the admin can reply at any time), but the Monday-prompt–gated weekly flow is the primary path.
- **Per-game time zone support.** All times are UTC. The `game_time` config remains a display string (e.g. "10:00 AM") and SFN timestamps are UTC-only.
- **Backfill of weekStatus items for past weeks.**

## Further Notes

- The `weekStatus` item lives in the Games table (PK = Monday date, SK = `"weekStatus"`), not a separate table. This is additive to the existing per-game items (PK = gameDate, SK = `"gameStatus"` / `"playerStatus#*"`).
- `create_game()` now uses `transact_write_items` (5 items: gameStatus + 3 playerStatus puts + weekStatus update) instead of `batch_write_item`. Existing callers of `create_game()` need no signature change — the weekStatus key is derived internally from the game date.
- The `GAME_LIFECYCLE_SFN_ARN` env var is the state machine ARN (not execution ARN). The execution ARN for stopping is derived by replacing `:stateMachine:` → `:execution:` and appending `:game-{gameDate}`.
- `sfn_timestamps_for_game(game_date: str) -> dict` is a pure utility function in `date_utils.py` that computes the four ISO-8601 timestamps needed in the SFN execution input.
- The Terraform `aws_scheduler_schedule` for `announcement_sender` is repurposed into `weekly_scheduler`. The `reminder_checker` and `game_finalizer` schedules are removed. A new `weekly_cutoff_checker` schedule is added (Tuesday 21:00 UTC).
- New Terraform IAM policies needed: SFN execution role (trust: states.amazonaws.com, can invoke the 4 task Lambdas); Lambda policy addition on `admin_processor` (sfn:StartExecution, sfn:StopExecution on the state machine ARN).
