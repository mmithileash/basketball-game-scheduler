"""Shared fixtures for integration tests running against LocalStack (port 4566).

All AWS SDK calls go through LocalStack via the AWS_ENDPOINT_URL env var.
Bedrock is the only service mocked at the Python level (not available in LocalStack).
"""

import importlib
import os

import boto3
import pytest

LOCALSTACK_ENDPOINT = "http://localhost:4566"

PLAYERS_TABLE = "Players"
GAMES_TABLE = "Games"
EMAIL_BUCKET = "game-emails"
SENDER_EMAIL = "scheduler@example.com"

# Handler strings must match the Terraform `handler` attribute in lambda.tf.
# Format: "<module>.<function>" — the Lambda runtime uses this to resolve the
# entry point.  Keyed by the source package name (src/<package>/).
LAMBDA_HANDLER_CONFIGS = {
    "announcement_sender": "handler.handler",
    "email_processor": "handler.handler",
    "reminder_checker": "handler.handler",
    "game_finalizer": "handler.handler",
}


def resolve_lambda_handler(package_name: str):
    """Resolve a handler string the way the Lambda runtime does.

    In the deployment zip the module sits at the root, but in our source tree
    it lives under ``<package_name>.handler``.  This function bridges that gap
    so tests exercise the exact function name that Terraform configures.
    """
    handler_string = LAMBDA_HANDLER_CONFIGS[package_name]
    module_name, func_name = handler_string.rsplit(".", 1)
    full_module = f"{package_name}.{module_name}"
    module = importlib.import_module(full_module)
    fn = getattr(module, func_name, None)
    if fn is None:
        raise AttributeError(
            f"Lambda handler '{handler_string}' not found: "
            f"{full_module}.{func_name} does not exist"
        )
    return fn


# ---------------------------------------------------------------------------
# Session-scoped: environment and infrastructure
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def localstack_endpoint():
    return LOCALSTACK_ENDPOINT


@pytest.fixture(autouse=True, scope="session")
def aws_credentials():
    """Set fake AWS credentials so boto3 never reaches real AWS."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-1"


@pytest.fixture(autouse=True, scope="session")
def env_vars():
    """Set application env vars consumed by common.config.load_config()."""
    os.environ["PLAYERS_TABLE"] = PLAYERS_TABLE
    os.environ["GAMES_TABLE"] = GAMES_TABLE
    os.environ["EMAIL_BUCKET"] = EMAIL_BUCKET
    os.environ["SENDER_EMAIL"] = SENDER_EMAIL
    os.environ["GAME_TIME"] = "10:00 AM"
    os.environ["GAME_LOCATION"] = "Main Court"
    os.environ["BEDROCK_MODEL_ID"] = "anthropic.claude-3-haiku-20240307-v1:0"
    # boto3 >= 1.34 honours this env var natively, routing every service call
    # (DynamoDB, S3, SES, ...) to LocalStack without code changes.
    os.environ["AWS_ENDPOINT_URL"] = LOCALSTACK_ENDPOINT


@pytest.fixture(scope="session")
def dynamodb_tables(aws_credentials, env_vars, localstack_endpoint):
    """Create both DynamoDB tables on LocalStack once per session."""
    dynamodb = boto3.resource("dynamodb", endpoint_url=localstack_endpoint,
                              region_name="eu-west-1")

    # Players table  (PK=email, SK=active)
    dynamodb.create_table(
        TableName=PLAYERS_TABLE,
        KeySchema=[
            {"AttributeName": "email", "KeyType": "HASH"},
            {"AttributeName": "active", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "email", "AttributeType": "S"},
            {"AttributeName": "active", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Games table  (PK=gameDate, SK=sk)
    dynamodb.create_table(
        TableName=GAMES_TABLE,
        KeySchema=[
            {"AttributeName": "gameDate", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "gameDate", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    yield dynamodb

    # Teardown
    dynamodb.Table(PLAYERS_TABLE).delete()
    dynamodb.Table(GAMES_TABLE).delete()


@pytest.fixture(scope="session")
def s3_bucket(aws_credentials, env_vars, localstack_endpoint):
    """Create the email S3 bucket on LocalStack once per session."""
    s3 = boto3.client("s3", endpoint_url=localstack_endpoint,
                      region_name="eu-west-1")
    s3.create_bucket(
        Bucket=EMAIL_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
    )
    yield s3


@pytest.fixture(scope="session")
def ses_identity(aws_credentials, env_vars, localstack_endpoint):
    """Verify sender email identity on LocalStack SES once per session."""
    ses = boto3.client("ses", endpoint_url=localstack_endpoint,
                       region_name="eu-west-1")
    ses.verify_email_identity(EmailAddress=SENDER_EMAIL)
    yield ses


# ---------------------------------------------------------------------------
# Function-scoped: reset module-level caches + seed data
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Reset the singleton caches in common modules so each test gets
    fresh boto3 clients that pick up the AWS_ENDPOINT_URL env var."""
    import common.dynamo as dynamo_mod
    import common.email_service as email_mod
    import common.bedrock_client as bedrock_mod
    import email_processor.handler as ep_handler

    dynamo_mod._config = None
    dynamo_mod._dynamodb = None
    dynamo_mod._client = None
    email_mod._config = None
    email_mod._ses_client = None
    bedrock_mod._config = None
    bedrock_mod._bedrock_client = None
    ep_handler._s3_client = None

    yield

    # Reset again after test to avoid cross-test pollution
    dynamo_mod._config = None
    dynamo_mod._dynamodb = None
    dynamo_mod._client = None
    email_mod._config = None
    email_mod._ses_client = None
    bedrock_mod._config = None
    bedrock_mod._bedrock_client = None
    ep_handler._s3_client = None


@pytest.fixture()
def seed_players(dynamodb_tables):
    """Insert 5 active and 2 inactive test players. Cleans up after the test."""
    table = dynamodb_tables.Table(PLAYERS_TABLE)

    players = [
        {"email": "alice@example.com", "name": "Alice", "active": "true"},
        {"email": "bob@example.com", "name": "Bob", "active": "true"},
        {"email": "charlie@example.com", "name": "Charlie", "active": "true"},
        {"email": "dave@example.com", "name": "Dave", "active": "true"},
        {"email": "eve@example.com", "name": "Eve", "active": "true"},
        {"email": "frank@example.com", "name": "Frank", "active": "false"},
        {"email": "grace@example.com", "name": "Grace", "active": "false"},
    ]

    for p in players:
        table.put_item(Item=p)

    yield players

    # Cleanup
    for p in players:
        table.delete_item(Key={"email": p["email"], "active": p["active"]})


@pytest.fixture()
def seed_game(dynamodb_tables):
    """Create a game with some RSVPs. Returns a helper dict with game details.

    The game has 3 YES players (alice, bob, charlie), 1 NO (dave), and
    eve has not responded (pending).
    """
    game_date = "2026-03-28"
    client = boto3.client("dynamodb", region_name="eu-west-1")

    items = [
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "gameStatus"},
                    "status": {"S": "OPEN"},
                    "createdAt": {"S": "2026-03-23T00:00:00+00:00"},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "playerStatus#YES"},
                    "players": {
                        "M": {
                            "alice@example.com": {"M": {"name": {"S": "Alice"}}},
                            "bob@example.com": {"M": {"name": {"S": "Bob"}}},
                            "charlie@example.com": {"M": {"name": {"S": "Charlie"}}},
                        }
                    },
                    "guests": {"L": []},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "playerStatus#NO"},
                    "players": {
                        "M": {
                            "dave@example.com": {"M": {"name": {"S": "Dave"}}},
                        }
                    },
                    "guests": {"L": []},
                }
            }
        },
        {
            "PutRequest": {
                "Item": {
                    "gameDate": {"S": game_date},
                    "sk": {"S": "playerStatus#MAYBE"},
                    "players": {"M": {}},
                    "guests": {"L": []},
                }
            }
        },
    ]

    client.batch_write_item(RequestItems={GAMES_TABLE: items})

    yield {
        "game_date": game_date,
        "yes_players": ["alice@example.com", "bob@example.com", "charlie@example.com"],
        "no_players": ["dave@example.com"],
        "maybe_players": [],
    }

    # Cleanup all game items
    table = dynamodb_tables.Table(GAMES_TABLE)
    for sk in ("gameStatus", "playerStatus#YES", "playerStatus#NO", "playerStatus#MAYBE"):
        table.delete_item(Key={"gameDate": game_date, "sk": sk})
