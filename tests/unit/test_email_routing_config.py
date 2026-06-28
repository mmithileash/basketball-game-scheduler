"""Config-level regression tests for inbound-email routing.

Inbound mail is split between two Lambdas purely by the SES receipt rule that
matches the *recipient* address (terraform/ses.tf):

    mail to admin_email  -> S3 admin/   -> admin_processor
    mail to sender_email -> S3 inbound/ -> email_processor

SES applies *every* matching rule (neither rule has a stop action), so if
admin_email == sender_email a single inbound email lands in BOTH prefixes and
fires BOTH Lambdas. A player's "No" then also hits admin_processor, which
rejects the non-admin sender with a spurious "not authorised" reply.

For the split to work the two channels must be distinct addresses AND each
admin-facing email that expects a reply must be sent *from* the admin address,
so the reply routes back to admin/ rather than to the player inbox.
"""

import re
from pathlib import Path

_TF = Path(__file__).resolve().parents[2] / "terraform"


def _tfvar(filename: str, name: str) -> str:
    text = (_TF / filename).read_text()
    m = re.search(rf'^{name}\s*=\s*"([^"]+)"', text, re.M)
    assert m, f"{name} not found in {filename}"
    return m.group(1)


def _tfvars_files() -> list[str]:
    """The committed template is always validated; the live tfvars (gitignored,
    so absent in CI) is validated too when it exists locally."""
    files = ["terraform.tfvars.example"]
    if (_TF / "terraform.tfvars").exists():
        files.append("terraform.tfvars")
    return files


def test_admin_and_sender_addresses_are_distinct():
    """admin/ and inbound/ rules key off these two vars; equal values collapse
    the channels so every email fires both processors."""
    for filename in _tfvars_files():
        sender_email = _tfvar(filename, "sender_email")
        admin_email = _tfvar(filename, "admin_email")
        assert sender_email != admin_email, (
            f"{filename}: sender_email == admin_email collapses the two SES "
            "receipt rules: a player reply hits admin_processor and gets a "
            "spurious 'not authorised'."
        )


def test_weekly_prompt_is_sent_from_the_admin_channel():
    """The weekly 'Schedule games?' prompt asks the admin to reply; its From
    address must be admin_email so the reply routes to admin/ (admin_processor),
    not inbound/ (email_processor)."""
    lambda_tf = (_TF / "lambda.tf").read_text()
    block = re.search(
        r'resource "aws_lambda_function" "weekly_scheduler".*?\n}',
        lambda_tf,
        re.S,
    )
    assert block, "weekly_scheduler resource not found in lambda.tf"
    # The sender override may be inline or via a shared admin env-var local; in
    # either case the resolved SENDER_EMAIL for this function must be admin_email.
    assert "var.admin_email" in block.group(0), (
        "weekly_scheduler must send the prompt from admin_email so admin "
        "replies route to admin_processor."
    )
