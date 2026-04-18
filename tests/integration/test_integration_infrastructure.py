"""Integration tests for Bridge infrastructure endpoints.

Covers the plumbing Ohmic hits at connect time and at steady state:
heartbeat, Bridge version, capability probe, log level round-trip,
and Ableton Live application version. These are independent of any
project state, so they run without fixtures that mutate tracks or
clips.

do not parallelize — all integration tests target the same Ableton
process, and running these concurrently with track/clip tests will
thrash shared state."""
import json

import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


# The canonical set of capability buckets Ohmic's MCP server asks about
# at connect time. If the Bridge adds a new bucket it must also be
# added here — otherwise the check below fires and the new bucket goes
# out the door without an integration test behind it.
EXPECTED_CAPABILITY_BUCKETS = {
    "clip_notes_rw",
    "clip_automation_envelopes",
    "arrangement_clips",
    "clip_slot_duplicate",
    "song_scale_properties",
    "scene_tempo",
    "scene_time_signature",
    "device_parameter_value_strings",
    "song_cue_points",
    "song_beat_listener",
    "browser",
}


def test_heartbeat_replies(osc):
    """/live/heartbeat returns a single-int tuple (1,) — see
    abletonosc/application.py. The exact value doesn't matter for
    callers, only that a reply arrives; still, assert the shape so a
    future change to the handler's return value surfaces as a failure."""
    reply = osc.query("/live/heartbeat", [])
    assert reply == (1,), (
        "heartbeat must reply (1,) — got %r" % (reply,)
    )


def test_bridge_version_reply(osc):
    """/live/api/ohmic/bridge_version returns the BRIDGE_VERSION tuple
    from manager.py. Ohmic compares this against its MIN_BRIDGE_VERSION
    on connect and refuses to proceed if the Bridge is older."""
    reply = osc.query("/live/api/ohmic/bridge_version", [])
    assert len(reply) == 3, (
        "bridge_version must be (major, minor, patch) — got %r" % (reply,)
    )
    major, minor, patch = reply
    assert isinstance(major, int) and isinstance(minor, int) and isinstance(patch, int), (
        "every version component must be an int — got %r" % (reply,)
    )
    assert (major, minor, patch) >= (0, 3, 0), (
        "bridge version must be >= (0, 3, 0) — got %r" % (reply,)
    )


def test_ohmic_capabilities_reply(osc):
    """/live/api/ohmic/capabilities returns a one-tuple containing a
    JSON-encoded dict of capability-bucket booleans. See
    abletonosc/capabilities.py for the canonical list. This test is a
    drift detector: if a future Bridge adds a bucket without updating
    EXPECTED_CAPABILITY_BUCKETS above it will fail here, forcing the
    author to wire integration coverage for the new surface."""
    reply = osc.query("/live/api/ohmic/capabilities", [])
    assert len(reply) == 1, (
        "capabilities reply must be a 1-tuple — got %r" % (reply,)
    )
    payload = reply[0]
    assert isinstance(payload, str), (
        "capabilities payload must be a JSON string — got %r" % (payload,)
    )
    decoded = json.loads(payload)
    assert isinstance(decoded, dict), (
        "decoded capabilities must be a dict — got %r" % (decoded,)
    )
    actual_keys = set(decoded.keys())
    assert actual_keys == EXPECTED_CAPABILITY_BUCKETS, (
        "capability bucket set drifted — expected %r, got %r "
        "(update EXPECTED_CAPABILITY_BUCKETS and add matching "
        "integration tests)"
        % (EXPECTED_CAPABILITY_BUCKETS, actual_keys)
    )
    for bucket, value in decoded.items():
        assert isinstance(value, bool), (
            "capability value for %r must be a bool — got %r"
            % (bucket, value)
        )


def test_log_level_roundtrip(osc):
    """Read current log level, set it to 'debug', verify via getter,
    restore the original, verify again. /live/api/set/log_level is
    fire-and-forget (the handler returns None), so the verification
    step is the only way to prove the write landed."""
    original_reply = osc.query("/live/api/get/log_level", [])
    assert len(original_reply) == 1, (
        "get/log_level must reply with a single string — got %r"
        % (original_reply,)
    )
    original_level = original_reply[0]
    assert isinstance(original_level, str)
    assert original_level in ("debug", "info", "warning", "error", "critical"), (
        "log level must be a known level — got %r" % (original_level,)
    )

    try:
        osc.send_message("/live/api/set/log_level", ["debug"])
        wait_one_tick()
        after_set = osc.query("/live/api/get/log_level", [])
        assert after_set == ("debug",), (
            "after set/log_level='debug', get/log_level must reply "
            "('debug',) — got %r" % (after_set,)
        )
    finally:
        # Restore — paired read-back so a failing restore is visible.
        osc.send_message("/live/api/set/log_level", [original_level])
        wait_one_tick()
        restored = osc.query("/live/api/get/log_level", [])
        assert restored == (original_level,), (
            "restore to original log level %r failed — got %r"
            % (original_level, restored)
        )


def test_application_version_reply(osc):
    """/live/application/get/version returns the Ableton Live
    application version. The handler in abletonosc/application.py
    returns (major, minor) — two integers. Assert the shape so a
    future Bridge change (e.g. adding patch) fails loudly here
    instead of silently changing the wire contract Ohmic depends on."""
    reply = osc.query("/live/application/get/version", [])
    assert len(reply) >= 2, (
        "application/get/version must reply with at least (major, minor) "
        "— got %r" % (reply,)
    )
    major, minor = int(reply[0]), int(reply[1])
    assert major >= 11, (
        "Ableton Live major version must be >= 11 — got %d" % major
    )
    assert minor >= 0
