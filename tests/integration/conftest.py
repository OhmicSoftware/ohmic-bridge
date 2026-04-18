"""Shared fixtures for Bridge integration tests.

Every test file in this folder has a module-level `pytestmark =
pytest.mark.integration` so `pytest` by default skips the whole folder.
Run with `pytest -m integration` while Ableton is running with
Ohmic_Bridge loaded."""
import os
import sys
import time

import pytest

# Add the Bridge repo root to sys.path so `from integration_client import ...`
# works regardless of which subfolder pytest is invoked from.
_bridge_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _bridge_root not in sys.path:
    sys.path.insert(0, _bridge_root)

from integration_client import AbletonOSCClient, TICK_DURATION, BridgeNotResponding


def wait_one_tick():
    """Sleep for one Ableton Live tick so a setter has time to land
    before a corresponding getter."""
    time.sleep(TICK_DURATION)


@pytest.fixture(scope="session")
def osc():
    """Shared OSC client for every integration test in this session.

    Health check on startup: if the Bridge isn't responding, fail fast
    with a useful message rather than letting every test time out
    individually."""
    client = AbletonOSCClient()
    try:
        client.query("/live/test", timeout=2.0)
    except BridgeNotResponding as e:
        client.stop()
        pytest.exit(
            "Integration tests require Ableton + Ohmic_Bridge. " + str(e),
            returncode=2,
        )
    yield client
    client.stop()
