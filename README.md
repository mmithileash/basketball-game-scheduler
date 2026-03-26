# Basketball Game Scheduler

An automated, email-based basketball game scheduler built on AWS serverless infrastructure. Every Monday it emails ~100 players about the upcoming Saturday game, processes replies using natural language understanding (AWS Bedrock + Claude), and manages RSVPs, reminders, and cancellations — all through email.

## How It Works

1. **Monday 9AM** — An announcement email is sent to all active players for Saturday's game
2. **Players reply** in natural language — "I'm in", "Can't make it", "I'll bring 2 friends", "Who's playing?"
3. **The system understands** the intent via Claude (Bedrock) and updates the roster accordingly
4. **Wed & Fri 9AM** — If fewer than 6 players have confirmed, reminders are sent. If still under 6 by Friday, the game is cancelled
5. **Friday with 6+** — Confirmation emails go out with the final roster

## Architecture

![Component Architecture](docs/diagrams/01_component_architecture.png)

Fully serverless on AWS (eu-west-1):

| Service | Role |
|---|---|
| **EventBridge** | Cron schedules (Mon, Wed, Fri) |
| **Lambda** (×3) | announcement-sender, email-processor, reminder-checker |
| **SES** | Send and receive emails |
| **S3** | Store raw inbound emails |
| **DynamoDB** (×2 tables) | Players + Games (including RSVPs) |
| **Bedrock** (Claude Haiku) | Parse player intent from free-text email replies |
| **Route 53** | Domain DNS + MX records for SES inbound |

Estimated monthly cost: **~$1.40–1.80**

See [docs/architecture.md](docs/architecture.md) for detailed flow diagrams, data model, and access patterns.

## Project Structure

```
├── src/
│   ├── common/                  # Shared modules
│   │   ├── config.py            # Environment-based configuration
│   │   ├── dynamo.py            # DynamoDB operations
│   │   ├── email_service.py     # SES email sending
│   │   └── bedrock_client.py    # Bedrock NLU intent parsing
│   ├── announcement_sender/     # Monday announcement Lambda
│   ├── email_processor/         # Inbound email processing Lambda
│   └── reminder_checker/        # Wed/Fri reminder & cancellation Lambda
├── terraform/                   # Infrastructure as Code
├── tests/
│   ├── unit/                    # Unit tests (moto mocks)
│   └── integration/             # Integration tests (LocalStack + Docker)
├── scripts/
│   ├── import_players.py        # CSV player import script
│   └── sample_players.csv       # Example player list
├── docs/
│   ├── architecture.md          # Detailed architecture documentation
│   └── diagrams/                # Architecture diagram PNGs
├── docker-compose.yml           # LocalStack for integration tests
├── Makefile                     # Build, test, and deploy commands
├── requirements.txt             # Production dependencies
└── requirements-dev.txt         # Development & test dependencies
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
3. **Enable Bedrock model access** — enable Claude Haiku 3 in the Bedrock console for eu-west-1

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
game_time      = "10:00 AM"
game_location  = "Community Center Court"
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
| `game_time` | Game time shown in announcements | `10:00 AM` |
| `game_location` | Game location shown in announcements | `TBD` |
| `bedrock_model_id` | Bedrock model for NLU | `anthropic.claude-3-haiku-20240307-v1:0` |
| `min_players` | Minimum players for a game to proceed | `6` |
| `environment` | Environment tag | `prod` |

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

## Data Model

Two DynamoDB tables with no GSIs:

**Players** — `PK: email, SK: active`

**Games** — `PK: gameDate (YYYY-MM-DD), SK: gameStatus | playerStatus#YES | playerStatus#NO | playerStatus#MAYBE`

See [docs/architecture.md](docs/architecture.md) for full schema and access patterns.
