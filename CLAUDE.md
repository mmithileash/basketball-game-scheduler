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

1. **Monday 9AM** — `announcement-sender` Lambda creates a game record in DynamoDB and emails all active players via SES
2. **Players reply** to emails with natural language — SES Receipt Rules store raw emails in S3, triggering `email-processor` Lambda
3. `email-processor` calls **AWS Bedrock (Claude Haiku)** to classify intent (JOIN/DECLINE/BRING_GUESTS/MAYBE/etc.), updates DynamoDB, and replies
4. **Wednesday & Friday 9AM** — `reminder-checker` Lambda sends reminders (Wed) or confirms/cancels the game (Fri) based on confirmed player count vs. `MIN_PLAYERS` threshold (default: 6)

### Lambda Functions (`src/`)

- `announcement_sender/handler.py` — Creates game + sends announcement emails (skips if pre-cancelled by admin)
- `email_processor/handler.py` — Parses S3-stored inbound email, calls Bedrock for NLU, updates roster, replies
- `reminder_checker/handler.py` — Counts confirmed players; sends reminders Wed, confirms or cancels Fri
- `admin_processor/handler.py` — Processes admin command emails (cancel game, add/deactivate/reactivate players)

### Shared Modules (`src/common/`)

- `config.py` — `Config` dataclass loaded from environment variables
- `dynamo.py` — All DynamoDB operations (create game, get/update roster, get pending players)
- `email_service.py` — SES send layer with templates for all email types
- `bedrock_client.py` — Bedrock integration; returns structured JSON `{intent, guest_count, guest_names, query_target, reply_draft}`

### DynamoDB Data Model

**Players table:** PK=`email`, SK=`active`

**Games table** (single-table): PK=`gameDate` (YYYY-MM-DD), SK varies:
- `gameStatus` → `{status: OPEN|CANCELLED|PLAYED, createdAt}`
- `playerStatus#YES` → map of `{email: {guests: [...]}}`
- `playerStatus#NO` → map of `{email: {}}`
- `playerStatus#MAYBE` → map of `{email: {}}`

### Infrastructure (`terraform/`)

Provisions: EventBridge cron rules, 3 Lambda functions, SES domain + receipt rules, S3 bucket (email storage), DynamoDB tables, Route 53 MX records, IAM roles.

Key variables (set in `terraform.tfvars`): `domain_name`, `sender_email`, `admin_email`, `game_time`, `game_location`, `bedrock_model_id`, `min_players`.

### Testing

- **Unit tests** (`tests/unit/`) — Use `moto` to mock AWS services; no external dependencies
- **Integration tests** (`tests/integration/`) — Use LocalStack via Docker Compose; test full end-to-end flows

#Python preferences
- Python version: 3.12.13 (managed by pyenv via `.python-version`)
- Use f-strings when possible



