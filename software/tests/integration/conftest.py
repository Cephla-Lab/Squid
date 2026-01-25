import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def enable_fast_test_timing() -> None:
    """Enable faster timing defaults for integration tests."""
    prior_fast = os.environ.get("SQUID_TEST_FAST")
    prior_speedup = os.environ.get("SQUID_TEST_SPEEDUP")

    if "SQUID_TEST_FAST" not in os.environ and "SQUID_TEST_SPEEDUP" not in os.environ:
        os.environ["SQUID_TEST_FAST"] = "1"

    yield

    if prior_fast is None:
        os.environ.pop("SQUID_TEST_FAST", None)
    else:
        os.environ["SQUID_TEST_FAST"] = prior_fast

    if prior_speedup is None:
        os.environ.pop("SQUID_TEST_SPEEDUP", None)
    else:
        os.environ["SQUID_TEST_SPEEDUP"] = prior_speedup
