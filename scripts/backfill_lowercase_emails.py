#!/usr/bin/env python3
"""
Backfill: lowercase existing Players-table email keys.

DynamoDB key lookups are exact-match, and the runtime now normalises every
sender/identity email to lowercase (see common.dynamo.normalize_email and
common.email_utils.extract_sender_email). Any pre-existing row whose ``email``
partition key contains an uppercase character would therefore silently never
match its owner again. This one-off scans the table and, for each such row,
writes a lowercase-keyed copy and deletes the original.

The partition key cannot be updated in place, so each rewrite is a Put (of a
copy with a lowercased ``email`` — and ``sponsorEmail`` if present) followed by
a Delete of the original. The Put is conditional on the lowercase item not
already existing, so a row that already has a lowercase twin is reported as a
COLLISION and left untouched for a human to resolve rather than clobbered.

Runs as a dry-run by default; pass --apply to make changes.

Usage:
    python scripts/backfill_lowercase_emails.py --table-name Players            # preview
    python scripts/backfill_lowercase_emails.py --table-name Players --apply    # execute
"""

import argparse
import sys

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError


def parse_args():
    parser = argparse.ArgumentParser(
        description="Lowercase existing Players-table email keys."
    )
    parser.add_argument(
        "--table-name",
        required=True,
        help="Name of the DynamoDB Players table.",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region (default: us-east-1).",
    )
    parser.add_argument(
        "--endpoint-url",
        default=None,
        help="Optional DynamoDB endpoint URL (e.g. http://localhost:4566 for LocalStack).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the rewrite. Without this flag the script only previews.",
    )
    return parser.parse_args()


def scan_all_items(table):
    """Yield every item in the table, paginating through the scan."""
    response = table.scan()
    yield from response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        yield from response.get("Items", [])


def needs_rewrite(item):
    """True if the item's email key has any uppercase character."""
    email = item.get("email", "")
    return email != email.lower()


def rewrite_item(table, item, apply):
    """Rewrite one item to a lowercase key. Returns 'rewritten', 'collision', or 'skipped'.

    Put (lowercased copy, conditional on not already existing) then Delete of the
    original. If the lowercase twin already exists, reports a collision and makes
    no changes.
    """
    old_email = item["email"]
    sk = item["active"]
    new_item = dict(item)
    new_item["email"] = old_email.lower()
    if "sponsorEmail" in new_item and isinstance(new_item["sponsorEmail"], str):
        new_item["sponsorEmail"] = new_item["sponsorEmail"].lower()

    if not apply:
        return "would-rewrite"

    try:
        # Fail rather than overwrite if a lowercase twin already exists.
        table.put_item(
            Item=new_item,
            ConditionExpression=Attr("email").not_exists() & Attr("active").not_exists(),
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "collision"
        raise

    table.delete_item(Key={"email": old_email, "active": sk})
    return "rewritten"


def main():
    args = parse_args()

    resource_kwargs = {"region_name": args.region}
    if args.endpoint_url:
        resource_kwargs["endpoint_url"] = args.endpoint_url
    table = boto3.resource("dynamodb", **resource_kwargs).Table(args.table_name)

    scanned = 0
    candidates = 0
    rewritten = 0
    collisions = []

    for item in scan_all_items(table):
        scanned += 1
        if not needs_rewrite(item):
            continue
        candidates += 1
        old_key = f"{item['email']} / {item['active']}"
        result = rewrite_item(table, item, args.apply)
        if result == "collision":
            collisions.append(old_key)
            print(f"  COLLISION (lowercase twin exists, left as-is): {old_key}")
        elif result == "rewritten":
            print(f"  rewrote: {old_key} -> {item['email'].lower()} / {item['active']}")
        else:  # would-rewrite (dry run)
            print(f"  would rewrite: {old_key} -> {item['email'].lower()} / {item['active']}")
        if result == "rewritten":
            rewritten += 1

    print(
        f"\nScanned {scanned} item(s); {candidates} needed lowercasing."
    )
    if args.apply:
        print(f"Rewrote {rewritten}; {len(collisions)} collision(s) left untouched.")
        if collisions:
            print("Resolve collisions manually, then re-run to finish.", file=sys.stderr)
            sys.exit(1)
    else:
        print("Dry run — no changes made. Re-run with --apply to execute.")


if __name__ == "__main__":
    main()
