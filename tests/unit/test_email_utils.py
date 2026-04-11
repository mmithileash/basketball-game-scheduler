import pytest

from common.email_utils import extract_sender_email


@pytest.mark.unit
def test_extract_plain_email():
    assert extract_sender_email("alice@example.com") == "alice@example.com"


@pytest.mark.unit
def test_extract_email_with_name():
    assert extract_sender_email("Alice <alice@example.com>") == "alice@example.com"


@pytest.mark.unit
def test_extract_email_with_name_and_spaces():
    assert extract_sender_email("  Alice Smith <alice@example.com>  ") == "alice@example.com"


@pytest.mark.unit
def test_extract_bare_email_strips_whitespace():
    assert extract_sender_email("  alice@example.com  ") == "alice@example.com"
