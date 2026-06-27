# PRD: Entity-Prefixed Partition Keys for the Games Table

Status: ready-for-agent
Scope: storage-layer naming refactor (no behavioral change)

## Problem Statement

The Games table is a single-table design whose partition key attribute is named
`gameDate`, but that attribute does not always hold a game date. For the
per-week aggregate row (`weekStatus`), the same `gameDate` attribute holds a
**Monday week-start date** instead. A developer reading `create_game` saw the
`weekStatus` row being written with a Monday value into a column called
`gameDate` and reasonably concluded the system was storing the wrong date for
games and emailing the wrong date to players.

It was a false alarm — the game record and all player-facing emails use the real
game date; only the separate `weekStatus` counter row is keyed by Monday, by
design — but the fact that an experienced reader was misled is the actual
problem. The attribute name lies about its contents, and that ambiguity will
keep costing reading time and inviting "fix" attempts against correct code.

## Solution

Make the storage layer self-describing so a partition key value tells you what
kind of entity it belongs to, and so the attribute name no longer claims every
row is a game.

- Rename the Games table partition-key attribute from `gameDate` to a neutral
  `pk`.
- Prefix the partition-key **values** with an entity token:
  - game rows: `GAME#<ISO date>` (e.g. `GAME#2026-06-27`)
  - week-status row: `WEEK#<ISO Monday>` (e.g. `WEEK#2026-06-29`)
- Keep all dates in ISO `YYYY-MM-DD` form (never locale formats like
  `DD/MM/YYYY`), preserving `date.fromisoformat` parsing and lexicographic
  ordering.

From the application's perspective nothing changes: handlers, Step Functions
input, the `game-{date}` execution name, and email templates keep working with
bare ISO dates. The prefix is an internal storage detail.

## User Stories

1. As a developer reading `create_game`, I want each partition-key value to
   announce its entity type, so that I can tell a game row from a week-status
   row at a glance without tracing the sort key.
2. As a developer inspecting raw DynamoDB items, I want the partition-key
   attribute to be named `pk` rather than `gameDate`, so that I am not misled
   into thinking a Monday value is a game date.
3. As a developer, I want the `GAME#`/`WEEK#` prefix logic to live in exactly
   one module (the DynamoDB access layer), so that I never have to remember to
   add or strip a prefix anywhere else.
4. As a developer writing a handler, I want to keep passing and receiving bare
   ISO dates, so that the storage refactor does not ripple into application code.
5. As a maintainer of `email_processor`, I want `get_open_games()` to keep
   returning a `gameDate` field holding a bare date, so that game
   disambiguation and routing code needs no changes.
6. As an operator, I want the atomic create-and-increment of the per-week
   `weekStatus` counter to keep working, so that scheduling multiple games in a
   week still accumulates `gameCount` correctly.
7. As an admin, I want games I schedule to continue announcing on the correct
   real date, so that players receive accurate dates — exactly as today.
8. As a player, I want the announcement, reminder, confirmation, and
   finalization emails to show the real game date, so that I show up on the
   right day — exactly as today.
9. As a developer, I want a single set of key-builder helpers
   (`game_pk`, `week_pk`, and a strip helper), so that key construction and
   parsing is centralized and testable.
10. As a developer, I want the sort-key vocabulary (`gameStatus`,
    `playerStatus#YES/NO/MAYBE`, `weekStatus`) left unchanged, so that the
    refactor stays scoped to the partition key.
11. As a developer running the unit suite, I want the moto table fixture and any
    key assertions updated to `pk` with prefixed values, so that tests reflect
    the new schema.
12. As an operator running Terraform, I want the Games table `hash_key` and
    attribute definition updated, so that the deployed table matches the code.
13. As a developer, I want the `weekStatus` read/write helpers to accept and
    return bare Monday dates while storing `WEEK#`-prefixed values internally,
    so that callers like `weekly_scheduler` and `weekly_cutoff_checker` are
    unaffected.
14. As a developer, I want `pre_cancel_game` and all roster mutation paths to go
    through the same key helpers, so that no write path bypasses the prefix
    convention.
15. As a developer, I want the boto3 `Key("gameDate")` query condition updated to
    `Key("pk")` against a prefixed game value, so that roster queries keep
    returning the right items.
16. As a future contributor, I want the design recorded, so that nobody
    re-flags the `weekStatus`-Monday row as a bug again.

## Implementation Decisions

- **Affected module:** the change is confined to the DynamoDB access layer
  (`common/dynamo.py`), plus Terraform table definition and tests. No
  application handler changes.
- **Schema change:** Games table partition-key attribute renamed `gameDate` →
  `pk`. Because a partition key cannot be altered in place, the table is
  recreated. This is acceptable: the environment is pre-launch with disposable
  data, so no data migration is required.
- **Key value encoding:**
  - game partition value: `GAME#<ISO date>`
  - week-status partition value: `WEEK#<ISO Monday>`
  - tokens are short uppercase (`GAME#`, `WEEK#`); the `#` delimiter matches the
    existing sort-key convention (`playerStatus#YES`).
- **Sort key unchanged:** `gameStatus`, `playerStatus#YES/NO/MAYBE`, and
  `weekStatus` remain exactly as-is. Within a game partition the sort key still
  separates the status row from the roster rows. On the `WEEK#` partition the
  `weekStatus` sort key becomes technically redundant with the prefix but is
  kept so every row stays uniformly composite-keyed.
- **Boundary discipline (core decision):** the `GAME#`/`WEEK#` prefix exists
  *only* inside the access layer. Every other layer — handlers, Step Functions
  execution input, the `game-{date}` execution name, email templates, and all
  function signatures — continues to deal in bare ISO dates. The access layer
  adds the prefix when building a `Key`/`Item` and strips it on every read
  before returning.
- **Key-builder helpers:** introduce `game_pk(date)`, `week_pk(monday)`, and a
  strip helper in the access layer; route every key/item construction site
  through them rather than inlining string literals.
- **Read-side contract (DTO):** read functions (`get_game_status`,
  `get_roster`, `get_open_games`, `get_week_status`, and friends) strip the
  prefix and continue to expose a friendly **`gameDate`** field holding the
  **bare** date. Storage is honest (`pk` with prefix); the returned object keeps
  the stable, app-friendly `gameDate` contract. Consequence: `email_processor`
  and other consumers that read `["gameDate"]` need zero changes.
- **Query update:** the roster query's boto3 condition changes from
  `Key("gameDate").eq(game_date)` to `Key("pk").eq(game_pk(game_date))`; the
  `Key("sk").begins_with("playerStatus#")` clause is unchanged.
- **Scan update:** `get_open_games` still filters on `sk = gameStatus AND
  status = OPEN`; it additionally strips the prefix from each returned item's
  partition value before exposing `gameDate`.
- **Atomicity preserved:** the create path keeps writing the four game rows and
  the `weekStatus` upsert in a single `transact_write_items`. The `weekStatus`
  upsert stays an `Update` with `if_not_exists(gameCount, :zero) + :one`
  (atomic accumulation across multiple games per week); only its key construction
  changes to `pk = week_pk(week_start)`.
- **Terraform:** update the Games table `hash_key` to `pk` and the attribute
  definition accordingly. IAM is unaffected (same single table).

## Testing Decisions

- **What makes a good test here:** assert on externally observable storage
  behavior through the access-layer API — e.g. "after `create_game`, the game is
  retrievable by its bare date and `get_open_games` returns it with a bare
  `gameDate`," and "scheduling two games in one week leaves `gameCount = 2`."
  Tests should exercise the public functions of the access layer, not reach into
  prefix string formatting; the prefix is an internal detail and asserting its
  literal form should be limited to a focused unit test on the key helpers.
- **Primary seam:** the DynamoDB access layer's public functions, exercised
  against a moto-mocked table. This is the highest existing seam and is already
  the seam used by the current `test_dynamo` suite.
- **Fixture update:** the moto Games table fixture must declare `pk` as the HASH
  key (replacing `gameDate`) in both the `KeySchema` and `AttributeDefinitions`.
  This is the one structural test change every other test inherits.
- **Modules to test:**
  - the access layer directly (`test_dynamo`): create, get game status, get
    roster, get open games, weekStatus get/update, pre-cancel, roster mutations.
  - the key helpers (a small focused unit test): `game_pk`/`week_pk` produce the
    expected prefixed ISO values; the strip helper round-trips.
  - regression coverage that the app contract is unchanged: `get_open_games`
    returns bare `gameDate`, consumed by `email_processor` disambiguation.
- **Prior art:** existing unit tests using moto in `test_dynamo`,
  `test_admin_processor`, `test_weekly_scheduler`, `test_weekly_cutoff_checker`,
  and `test_game_lifecycle`, plus the shared table fixtures in
  `tests/unit/conftest.py`. Integration tests under `tests/integration/` use
  LocalStack and create the table in their own conftest; that table definition
  must mirror the `pk` schema change.

## Out of Scope

- Any change to the Players table.
- Any change to the sort-key vocabulary or the `playerStatus#` encoding.
- Splitting `weekStatus` into a separate table (an alternative that was
  considered and rejected in favor of staying single-table with prefixes).
- Changing how dates are resolved from admin natural-language replies (the
  separate "which Tuesday does the admin mean" question raised during design is
  not addressed here).
- Any data migration tooling — the environment is disposable/pre-launch, so the
  table is simply recreated.
- Application-handler logic, email templates, Step Functions definition, and the
  per-game lifecycle behavior — all unchanged.

## Further Notes

- Origin: this PRD came out of a design interview that started from a suspected
  bug ("game creation only stores the Monday week date and emails the wrong
  date"). Investigation showed the game record and emails already use the real
  date; line-149's `weekStatus` row keyed by Monday is intentional and the
  `Update`/`if_not_exists` upsert is correct (it must accumulate `gameCount`
  across multiple games per week, which a `Put` would clobber). The work item is
  therefore a clarity refactor, not a bug fix.
- Rejected alternative: splitting `weekStatus` into its own honestly-named
  `weekStartDate` table. It is arguably the most "honest" model (no single-table
  benefit is actually being exploited, since week rows and game rows are never
  queried together, and `transact_write_items` works across tables), but the
  developer preferred to keep one table with entity-prefixed values.
- Date format guardrail: tokens must use ISO dates. A locale format such as
  `27/06/2026` would break `date.fromisoformat` and lexicographic key ordering
  and must not be used.
