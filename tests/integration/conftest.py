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


# --------------------------------------------------------------------------
# clip_trigger_quantization wire format
# --------------------------------------------------------------------------
# Live.Song.Song.clip_trigger_quantization is a Live.Song.Quantization
# enum, which is serialized over OSC as an integer (0..13). Integer 0
# corresponds to Live.Song.Quantization.q_no_q ("None" in the UI) which
# is what we need for the transport / clip-slot / scene fire tests so
# start_playing / fire / stop_playing flip is_playing immediately
# instead of waiting for the next quantized boundary. See
# abletonosc/song.py — the property is registered under properties_rw
# and the handler just does setattr(target, prop, params[0]).
#
# We cannot assume the property accepts a string: Live's enum setter
# rejects strings. Tests here treat the value as an int.
_QUANTIZATION_NONE = 0


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


@pytest.fixture
def _quantization_none(osc):
    """Set clip_trigger_quantization to 'none' (int 0) so transport
    state changes (start_playing / stop_playing / clip_slot.fire /
    scene.fire) take effect immediately instead of waiting for the
    next quantized boundary. Restore the original value on teardown
    with read-back verification.

    Shared across test_integration_song_transport.py,
    test_integration_clip_slot.py, and test_integration_scene.py — any
    test that mutates transport state and needs the flip to be
    observable within ~0.3s should depend on this fixture.
    """
    probe = osc.query("/live/song/get/clip_trigger_quantization", [])
    assert len(probe) >= 1, (
        "clip_trigger_quantization read returned empty reply: %r" % (probe,)
    )
    original_value = probe[-1]

    osc.send_message(
        "/live/song/set/clip_trigger_quantization",
        [_QUANTIZATION_NONE],
    )
    wait_one_tick()
    after_set = osc.query("/live/song/get/clip_trigger_quantization", [])
    assert after_set[-1] == _QUANTIZATION_NONE, (
        "set clip_trigger_quantization=%r did not land — got %r"
        % (_QUANTIZATION_NONE, after_set)
    )

    yield

    osc.send_message(
        "/live/song/set/clip_trigger_quantization",
        [original_value],
    )
    wait_one_tick()
    restored = osc.query("/live/song/get/clip_trigger_quantization", [])
    assert restored[-1] == original_value, (
        "restore of clip_trigger_quantization failed — got %r, expected %r"
        % (restored[-1], original_value)
    )
