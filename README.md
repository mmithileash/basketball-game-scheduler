# Basketball Game Scheduler

An automated, email-based basketball game scheduler built on AWS serverless infrastructure. Each week the admin decides whether to schedule one or more games; once scheduled, a Step Functions execution drives that game through announcement, reminders, confirmation, and finalisation. Player replies are processed using natural language understanding (AWS Bedrock + Claude) to manage RSVPs — all through email.

## How It Works

1. **Monday 9AM UTC** — `weekly-scheduler` checks the **current** week; if it has fewer than the target number of live games (`min_games_per_week`, a floor) and no no-game decision is recorded, it emails active admins asking whether to schedule game(s). Games can be arranged and played in the same week.
2. **Admin replies** in natural language — "Tuesday and Saturday", "No games this week" — `admin-processor` parses the command via Bedrock and either creates the game(s) (starting a per-game Step Functions execution) or marks the week as having no games. Any game starting less than 48h away, or with an unparseable time, holds the whole batch with an ask to resend. A future-week date is allowed and tracked on its own week.
3. **Tuesday 9PM UTC** — `weekly-cutoff-checker` notifies all players **only when the current week genuinely ends with zero games** and no decision was already recorded
4. **Per-game lifecycle** (`basketball-game-lifecycle` Step Functions execution): the announce/reminder/confirm/finalize moments are **adaptive**, anchored to the game's real start and scaling between a compressed 48h/36h/24h floor (same-week games) and a 7d/4d/2d cap (far-out games). Announce sends a tentative announcement (at creation for near-term games) showing the exact RSVP cutoff and the per-hour cost (`€45/hour`, or `Free`); reminder sends a low-signup nudge if needed; confirm (cutoff floored to the hour) makes the go/no-go decision (cancelling, or resolving the turnout tier, locking in the game's start time and duration, and emailing the full total cost with the confirmed player count); and after the game's actual end it is marked `PLAYED`
5. **Players reply** in natural language — "I'm in", "Can't make it", "I'll bring 2 friends", "Who's playing?" — to whichever game's email thread they're responding to (the system disambiguates when multiple games are open at once)
6. **The system understands** the intent via Claude (Bedrock) and updates that game's roster accordingly
7. **Admins** can email `admin@<domain>` at any time to schedule/cancel games, add players, or deactivate/reactivate players

## Architecture

Fully serverless on AWS (eu-west-1):

| Service | Role |
|---|---|
| **EventBridge Scheduler** | Monday 9AM UTC weekly prompt + Tuesday 9PM UTC cutoff check |
| **Step Functions** | One execution per game (`basketball-game-lifecycle`), driving announce → reminder → confirm/cancel → finalize |
| **Lambda** (×8) | `weekly_scheduler`, `weekly_cutoff_checker`, `email_processor`, `admin_processor`, and 4 game-lifecycle task Lambdas |
| **SES** | Send and receive emails |
| **S3** | Store raw inbound emails (prefix-routed: `admin/` → admin, catch-all → players) |
| **DynamoDB** (×2 tables) | Players + Games (including RSVPs and weekly scheduling counters) |
| **Bedrock** (Claude Haiku) | Parse admin scheduling commands and player intent from free-text emails |
| **Route 53** | Domain DNS + MX records for SES inbound |

See [docs/architecture.md](docs/architecture.md) for detailed flow descriptions, data model, and access patterns.

## Project Structure

```
├── src/
│   ├── common/                      # Shared modules
│   │   ├── config.py                # Environment-based configuration
│   │   ├── date_utils.py            # Week/SFN-timestamp helpers
│   │   ├── dynamo.py                 # DynamoDB operations
│   │   ├── email_service.py         # SES email sending
│   │   ├── email_utils.py           # Inbound email parsing/quote stripping
│   │   ├── policy.py                 # Per-game timing policy helpers
│   │   └── bedrock_client.py        # Bedrock NLU intent parsing
│   ├── weekly_scheduler/            # Monday admin-prompt Lambda
│   ├── weekly_cutoff_checker/       # Tuesday no-response fallback Lambda
│   ├── game_lifecycle/              # Per-game SFN task Lambdas
│   │   ├── announce_task.py
│   │   ├── reminder_task.py
│   │   ├── confirm_or_cancel_task.py
│   │   └── finalize_task.py
│   ├── email_processor/             # Inbound player email processing Lambda
│   └── admin_processor/             # Admin command email Lambda
├── terraform/                       # Infrastructure as Code (incl. Step Functions state machine)
├── tests/
│   ├── unit/                        # Unit tests (moto mocks)
│   └── integration/                 # Integration tests (LocalStack + Docker)
├── scripts/
│   └── import_players.py            # CSV player import script
├── docs/
│   └── architecture.md              # Detailed architecture documentation
├── docker-compose.yml                # LocalStack for integration tests
├── Makefile                          # Build, test, and deploy commands
├── requirements.txt                  # Production dependencies
└── requirements-dev.txt              # Development & test dependencies
```

## Prerequisites

- **Python 3.12**
- **Docker** (for integration tests)
- **Terraform** (for infrastructure provisioning)
- **AWS account** (eu-west-1 region)
- **Registered domain** — required for SES inbound email (e.g. a cheap `.link` or `.xyz` via Route 53)

### AWS Setup (one-time manual steps)

1. **Register a domain** via Route 53 (or transfer an existing one)
2. **Exit SES sandbox** — submit a support request in the AWS console to enable sending to unverified email addresses
3. **Enable Bedrock model access** — enable the configured Claude Haiku model in the Bedrock console for eu-west-1

## Getting Started

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
```

### 2. Run tests

```bash
# Unit tests (no Docker needed)
make test-unit

# Integration tests (requires Docker)
make test-integration

# All tests
make test-all
```

### 3. Deploy infrastructure

```bash
make tf-init
make tf-plan    # review changes
make tf-apply   # deploy
```

Terraform will prompt for required variables, or create a `terraform.tfvars` file:

```hcl
domain_name    = "yourdomain.com"
sender_email   = "scheduler@yourdomain.com"
admin_email    = "admin@yourdomain.com"
default_game_location = "Community Center Court"
default_game_map_url  = "https://www.google.com/maps/place/Your+Venue"
default_game_hourly_cost = 45
```

### 4. Import players

Prepare a CSV with `email,name` columns (name is optional):

```csv
email,name
john@example.com,John
jane@example.com,Jane
player3@example.com,
```

Import into DynamoDB:

```bash
python scripts/import_players.py \
    --csv-file your_players.csv \
    --table-name Players \
    --region eu-west-1
```

### 5. Update Route 53 nameservers

After `terraform apply`, update your domain registrar's nameservers to the ones output by Terraform. This enables SES to receive inbound emails.

## Configuration

| Variable | Description | Default |
|---|---|---|
| `domain_name` | Domain for SES email | *(required)* |
| `sender_email` | From address for outgoing emails | *(required)* |
| `admin_email` | Admin command inbox (`admin@<domain>`) | *(required)* |
| `default_game_location` | Default venue for games scheduled without an explicit location | `TBD` |
| `default_game_map_url` | Optional map link for the default location | `""` |
| `default_game_hourly_cost` | Default per-hour game cost (euros) for games scheduled without a stated cost; may be fractional, `0` = free | `45` |
| `bedrock_model_id` | Bedrock inference profile for NLU | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `min_players` | Minimum confirmed players for a game to proceed | `6` |
| `long_game_threshold` | Confirmed count at/above which the long-game tier applies (otherwise the short-game tier) | `10` |
| `long_game_start_time` | Start time for the long-game tier | `10:00 AM` |
| `long_game_duration_hours` | Duration (hours) for the long-game tier | `2` |
| `short_game_start_time` | Start time for the short-game tier | `11:00 AM` |
| `short_game_duration_hours` | Duration (hours) for the short-game tier | `1` |
| `min_games_per_week` | Target games per week (a floor): the Monday prompt fires while the current week has fewer than this many live games | `1` |
| `environment` | Environment tag | `prod` |

These threshold and tier start/duration values seed each game's **policy** at creation (the default two-tier policy). They are not read at runtime — a game carries its own policy on the record. An admin can override a specific game with a fixed start time and duration when scheduling (see [Admin Commands](#admin-commands)); supplying exactly one of the two is rejected and holds the whole batch.

## Supported Player Intents

Players reply to emails in natural language. The system understands:

| What the player says | What happens |
|---|---|
| "I'm in" / "Count me in" | Marked as confirmed |
| "Can't make it" / "I'm out" | Marked as declined |
| "Maybe" / "Not sure yet" | Marked as maybe |
| "I'll bring 2 friends, Mike and Sarah" | Confirmed with 2 guests |
| "Who's playing?" | Receives current roster |
| "Is John coming?" | Receives that player's status |

Only registered players and known guests (those with their own contact email) can interact with the system. Unknown senders receive a rejection email with no roster data leaked.

When more than one game is open at once, replies are matched to the right game via a `[Game: YYYY-MM-DD]` subject marker on outbound emails, falling back to single-open-game inference, a Bedrock-derived date hint, or an explicit clarification request.

Guests with a contact email can also reply to cancel their attendance ("Can't make it") or query the roster ("Who's playing?"). When a guest cancels, their sponsor is notified. Guests with a contact email also receive the final confirmation and cancellation emails directly.

## Admin Commands

Admins email `admin@<domain>` in natural language. Admin status is stored in DynamoDB (not config) so admins can be added at runtime without redeployment.

| What the admin says | What happens |
|---|---|
| "Tuesday and Saturday" *(in response to the weekly prompt)* | Creates both games (each with the default two-tier policy) and starts a Step Functions execution for each |
| "Saturday, 10am for 2 hours" | Creates a **fixed** game pinned to that start time and duration (equal tiers — no turnout branching) |
| "Tuesday at the YMCA, map https://maps.app/xyz, €50 per hour" | Creates the game with that venue and per-hour cost snapshotted onto it (a custom venue needs both a name and a map link; cost must be a per-hour amount, `€0` for a free game) |
| "No games this week" | Marks the week as no-game; players are notified |
| "Cancel the game on 2026-04-19" (before announcement) | Game pre-cancelled directly in DynamoDB |
| "Cancel the game on 2026-04-19" (after announcement) | Game cancelled, its Step Functions execution stopped, YES/MAYBE players and guests notified immediately |
| "Add player alice@example.com, name Alice" | Alice added as an active player |
| "Add admin bob@example.com, name Bob" | Bob added as an active admin |
| "Deactivate charlie@example.com" | Charlie deactivated; no longer receives game emails |
| "Reactivate charlie@example.com" | Charlie reactivated |

Non-admins who email the admin address receive a rejection email.

## Data Model

Two DynamoDB tables with no GSIs:

**Players** — `PK: email, SK: active`

**Games** — `PK: pk (entity-prefixed: GAME#<YYYY-MM-DD> for a game, WEEK#<Monday YYYY-MM-DD> for weekStatus items), SK: gameStatus | playerStatus#YES | playerStatus#NO | playerStatus#MAYBE | weekStatus`

The `GAME#`/`WEEK#` prefix is an internal storage detail confined to `common/dynamo.py`; handlers and emails deal in bare ISO dates throughout. A week's game count is computed live from its non-cancelled game rows (no stored counter); the `weekStatus` row persists only a no-game decision (`reason: no_response | admin_declined`) to keep the Tuesday cutoff idempotent.

See [docs/architecture.md](docs/architecture.md) for full schema and access patterns.
