"""Contract tests: verify that Terraform handler strings resolve to callable functions.

If someone renames a handler function in Python or changes the handler attribute
in lambda.tf without updating the other, these tests will fail.
"""

import pytest

from tests.integration.conftest import LAMBDA_HANDLER_CONFIGS, resolve_lambda_handler


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("package_name", LAMBDA_HANDLER_CONFIGS.keys())
def test_handler_string_resolves_to_callable(package_name):
    """The handler string for each Lambda must resolve to a callable function."""
    handler_fn = resolve_lambda_handler(package_name)
    assert callable(handler_fn), (
        f"Handler for '{package_name}' resolved to {handler_fn!r}, which is not callable"
    )
