#!/usr/bin/env python3
"""
Import players from a CSV file into the DynamoDB Players table.

CSV format:
    email,name
    player@example.com,John Doe
    another@example.com,

The 'name' column is optional. If missing or empty, name is stored as null.

Usage:
    python scripts/import_players.py --csv-file scripts/sample_players.csv --table-name Players
"""

import argparse
import csv
import sys

import boto3


BATCH_SIZE = 25


def parse_args():
    parser = argparse.ArgumentParser(
        description="Import players from a CSV file into DynamoDB Players table."
    )
    parser.add_argument(
        "--csv-file",
        required=True,
        help="Path to the CSV file containing player data.",
    )
    parser.add_argument(
        "--table-name",
        required=True,
        help="Name of the DynamoDB Players table.",
    )
    parser.add_argument(
        "--region",
        default="eu-west-1",
        help="AWS region (default: eu-west-1).",
    )
    parser.add_argument(
        "--endpoint-url",
        default=None,
        help="Optional DynamoDB endpoint URL (e.g. http://localhost:4566 for LocalStack).",
    )
    return parser.parse_args()


def read_players_from_csv(csv_file):
    """Read players from a CSV file. Returns a list of dicts with 'email' and optional 'name'."""
    players = []
    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        # Validate that 'email' column exists
        if "email" not in reader.fieldnames:
            print("Error: CSV file must have an 'email' column.", file=sys.stderr)
            sys.exit(1)

        has_name_column = "name" in reader.fieldnames

        for row in reader:
            email = row["email"].strip()
            if not email:
                continue  # Skip rows with empty email

            name = None
            if has_name_column:
                raw_name = row["name"].strip()
                if raw_name:
                    name = raw_name

            players.append({"email": email, "name": name})

    return players


def build_put_request(player, table_name):
    """Build a DynamoDB PutRequest for a player."""
    item = {
        "email": {"S": player["email"]},
        "active": {"S": "true"},
    }
    if player["name"] is not None:
        item["name"] = {"S": player["name"]}
    else:
        item["name"] = {"NULL": True}

    return {"PutRequest": {"Item": item}}


def import_players(players, table_name, dynamodb_client):
    """Import players into DynamoDB using batch_write_item in batches of 25."""
    total = len(players)
    imported = 0

    for i in range(0, total, BATCH_SIZE):
        batch = players[i : i + BATCH_SIZE]
        request_items = {
            table_name: [build_put_request(p, table_name) for p in batch]
        }

        # Handle unprocessed items with retries
        while request_items:
            response = dynamodb_client.batch_write_item(RequestItems=request_items)
            unprocessed = response.get("UnprocessedItems", {})
            if unprocessed:
                request_items = unprocessed
            else:
                break

        imported += len(batch)
        print(f"Imported {imported} of {total} players")


def main():
    args = parse_args()

    players = read_players_from_csv(args.csv_file)
    if not players:
        print("No players found in CSV file.")
        sys.exit(0)

    print(f"Found {len(players)} players in {args.csv_file}")

    client_kwargs = {"region_name": args.region}
    if args.endpoint_url:
        client_kwargs["endpoint_url"] = args.endpoint_url

    dynamodb_client = boto3.client("dynamodb", **client_kwargs)

    import_players(players, args.table_name, dynamodb_client)
    print("Import complete.")


if __name__ == "__main__":
    main()
