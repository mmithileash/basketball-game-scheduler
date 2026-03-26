import os

import boto3
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def set_env_vars(monkeypatch):
    """Set all required environment variables for tests."""
    monkeypatch.setenv("PLAYERS_TABLE", "test-players")
    monkeypatch.setenv("GAMES_TABLE", "test-games")
    monkeypatch.setenv("EMAIL_BUCKET", "test-email-bucket")
    monkeypatch.setenv("SENDER_EMAIL", "scheduler@example.com")
    monkeypatch.setenv("GAME_TIME", "10:00 AM")
    monkeypatch.setenv("GAME_LOCATION", "Main Court")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")

    # Reset module-level caches so each test gets fresh clients/config
    import common.dynamo as dynamo_mod
    import common.email_service as email_mod
    import common.bedrock_client as bedrock_mod

    dynamo_mod._config = None
    dynamo_mod._dynamodb = None
    dynamo_mod._client = None
    email_mod._config = None
    email_mod._ses_client = None
    bedrock_mod._config = None
    bedrock_mod._bedrock_client = None


@pytest.fixture
def dynamodb_tables():
    """Create both DynamoDB tables using moto."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")

        # Create Players table
        dynamodb.create_table(
            TableName="test-players",
            KeySchema=[
                {"AttributeName": "email", "KeyType": "HASH"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "email", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create Games table
        dynamodb.create_table(
            TableName="test-games",
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

        # Reset dynamo module caches so they pick up moto clients
        import common.dynamo as dynamo_mod
        dynamo_mod._config = None
        dynamo_mod._dynamodb = None
        dynamo_mod._client = None

        yield dynamodb


@pytest.fixture
def s3_bucket():
    """Create the S3 bucket using moto."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-west-1")
        s3.create_bucket(
            Bucket="test-email-bucket",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
        )
        yield s3


@pytest.fixture
def ses_setup():
    """Verify sender email identity using moto."""
    with mock_aws():
        ses = boto3.client("ses", region_name="eu-west-1")
        ses.verify_email_identity(EmailAddress="scheduler@example.com")

        # Reset email_service module cache
        import common.email_service as email_mod
        email_mod._config = None
        email_mod._ses_client = None

        yield ses


@pytest.fixture
def sample_players():
    """Return a list of test player dicts."""
    return [
        {"email": "alice@example.com", "name": "Alice", "active": "true"},
        {"email": "bob@example.com", "name": "Bob", "active": "true"},
        {"email": "charlie@example.com", "name": "Charlie", "active": "true"},
        {"email": "dave@example.com", "name": None, "active": "true"},
        {"email": "eve@example.com", "name": "Eve", "active": "false"},
    ]


@pytest.fixture
def sample_game_date():
    """Return a test date string."""
    return "2026-03-28"
