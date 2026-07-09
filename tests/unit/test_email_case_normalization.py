"""Regression: registered player must not be rejected on email-case mismatch.

Bug: get_sender_role did an exact-match, case-sensitive DynamoDB lookup, so a
player stored as 'Foo@Bar.com' replying as 'foo@bar.com' (or vice-versa)
resolved to 'unknown' -> "You are not a registered player". Emails are now
normalised to lowercase on every write and lookup of an identity key.
"""
import boto3
import pytest
from moto import mock_aws

from common.dynamo import (
    add_player,
    get_player_name,
    get_sender_role,
    is_admin,
)


def _create_players_table():
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    dynamodb.create_table(
        TableName="test-players",
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
    return dynamodb


def _reset_caches():
    import common.dynamo as dynamo_mod
    dynamo_mod._config = None
    dynamo_mod._dynamodb = None
    dynamo_mod._client = None


@pytest.mark.unit
@mock_aws
def test_mixed_case_write_matches_lowercase_lookup():
    """Player added with mixed-case email is found by a lowercase reply."""
    _reset_caches()
    _create_players_table()

    add_player("Mithil.Mohan@Gmail.com", "Mithil")

    assert get_sender_role("mithil.mohan@gmail.com") == "player"
    assert get_player_name("mithil.mohan@gmail.com") == "Mithil"


@pytest.mark.unit
@mock_aws
def test_lowercase_write_matches_mixed_case_lookup():
    """Player added lowercase is found even if the reply casing differs."""
    _reset_caches()
    _create_players_table()

    add_player("mithil.mohan@gmail.com", "Mithil", is_admin=True)

    assert get_sender_role("Mithil.Mohan@GMAIL.com") == "player"
    assert is_admin("MITHIL.MOHAN@gmail.com") is True
