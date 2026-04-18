"""Integration tests for track property endpoints.

Covers name/mute round-trip, has_midi_input, devices/name, delete_device,
and track.stop_all_clips. All tests target TRACK_ID = 0 which must be a
MIDI track in the test project — skip cleanly if not.

Note on /live/track/load/device: the Bridge does NOT register this
endpoint (confirmed against abletonosc/track.py — only delete_device
and stop_all_clips are exposed as methods). Loading a device onto a
track is done via /live/browser/load, which is already covered by
test_integration_browser.py::test_browser_load_instrument_then_read_back.
The test for /live/track/load/device in this file therefore skips with
a clear reason rather than faking success.

do not parallelize — these tests mutate track 0's name, mute state,
and device chain. Running them alongside other track-touching tests
will thrash shared state.
"""
import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration

TRACK_ID = 0


def _require_midi_track_0(osc):
    """Skip the test if track 0 is not a MIDI track (most endpoints
    under test here assume a MIDI track). Not a Bridge failure —
    project-setup precondition."""
    has_midi = osc.query("/live/track/get/has_midi_input", [TRACK_ID])
    # Wire format: (track_id, bool).
    if not (len(has_midi) >= 2 and bool(has_midi[1])):
        pytest.skip(
            "track %d is not a MIDI track in the current project — "
            "the track-property integration suite expects track 0 to "
            "be MIDI. Move a MIDI track to index 0 and re-run."
            % TRACK_ID
        )


# --------------------------------------------------------------------------
# Track name round-trip
# --------------------------------------------------------------------------
def test_track_name_roundtrip(osc):
    """Save track name, set to a distinctive sentinel, verify via
    read-back, restore, verify restore."""
    _require_midi_track_0(osc)

    probe = osc.query("/live/track/get/name", [TRACK_ID])
    assert len(probe) >= 2, "track name read was incomplete: %r" % (probe,)
    original = str(probe[1])
    sentinel = "__Integration Test Track__"

    try:
        osc.send_message("/live/track/set/name", [TRACK_ID, sentinel])
        wait_one_tick()
        after_set = osc.query("/live/track/get/name", [TRACK_ID])
        assert str(after_set[1]) == sentinel, (
            "track name set did not land — expected %r, got %r"
            % (sentinel, after_set)
        )
    finally:
        osc.send_message("/live/track/set/name", [TRACK_ID, original])
        wait_one_tick()
        restored = osc.query("/live/track/get/name", [TRACK_ID])
        assert str(restored[1]) == original, (
            "track name restore failed — expected %r, got %r"
            % (original, restored)
        )


# --------------------------------------------------------------------------
# Track mute round-trip
# --------------------------------------------------------------------------
def test_track_mute_roundtrip(osc):
    """Save mute state, flip to True, verify, flip to False, verify,
    restore to original, verify. Three verified writes."""
    _require_midi_track_0(osc)

    probe = osc.query("/live/track/get/mute", [TRACK_ID])
    assert len(probe) >= 2, "track mute read was incomplete: %r" % (probe,)
    # Live returns bools; some OSC wire encodings surface them as 0/1.
    # Normalize to bool for comparison.
    original = bool(probe[1])

    try:
        osc.send_message("/live/track/set/mute", [TRACK_ID, True])
        wait_one_tick()
        after_true = osc.query("/live/track/get/mute", [TRACK_ID])
        assert bool(after_true[1]) is True, (
            "mute=True did not land — got %r" % (after_true,)
        )

        osc.send_message("/live/track/set/mute", [TRACK_ID, False])
        wait_one_tick()
        after_false = osc.query("/live/track/get/mute", [TRACK_ID])
        assert bool(after_false[1]) is False, (
            "mute=False did not land — got %r" % (after_false,)
        )
    finally:
        osc.send_message("/live/track/set/mute", [TRACK_ID, original])
        wait_one_tick()
        restored = osc.query("/live/track/get/mute", [TRACK_ID])
        assert bool(restored[1]) is original, (
            "mute restore failed — expected %r, got %r"
            % (original, restored)
        )


# --------------------------------------------------------------------------
# Read-only track properties
# --------------------------------------------------------------------------
def test_track_has_midi_input_read(osc):
    """/live/track/get/has_midi_input returns (track_id, bool). Pure
    read; no mutation."""
    reply = osc.query("/live/track/get/has_midi_input", [TRACK_ID])
    assert len(reply) >= 2, (
        "has_midi_input reply was incomplete: %r" % (reply,)
    )
    assert int(reply[0]) == TRACK_ID
    # Live returns a native bool, but some encodings surface 0/1 ints.
    assert isinstance(reply[1], (bool, int)), (
        "has_midi_input value must be bool/int — got %r (type %s)"
        % (reply[1], type(reply[1]).__name__)
    )


def test_track_devices_name_read(osc):
    """/live/track/get/devices/name returns (track_id, name_0, ...).
    On a track with no devices, the tuple is length 1 (just track_id).
    """
    reply = osc.query("/live/track/get/devices/name", [TRACK_ID])
    assert len(reply) >= 1, (
        "devices/name reply was empty: %r" % (reply,)
    )
    assert int(reply[0]) == TRACK_ID, (
        "first element must be the track id — got %r" % (reply,)
    )
    # Every remaining element must be a string (device name).
    for name in reply[1:]:
        assert isinstance(name, str), (
            "device name must be a string — got %r (type %s) in %r"
            % (name, type(name).__name__, reply)
        )


# --------------------------------------------------------------------------
# Track stop_all_clips
# --------------------------------------------------------------------------
def _query_clip_trigger_quantization(osc):
    reply = osc.query("/live/song/get/clip_trigger_quantization", [])
    assert len(reply) >= 1
    return reply[-1]


def test_track_stop_all_clips_stops_track_clip(osc):
    """Fire the clip at (TRACK_ID, 0), verify is_playing=True,
    send /live/track/stop_all_clips for TRACK_ID, verify is_playing=
    False. Requires quantization=None so the fire/stop flip
    immediately. Manages its own quantization save/restore so this
    test file doesn't need to depend on another file's fixture."""
    import time

    _require_midi_track_0(osc)

    # Precondition: clip exists at (TRACK_ID, 0).
    has_clip = osc.query("/live/clip_slot/get/has_clip", [TRACK_ID, 0])
    if not (len(has_clip) >= 3 and bool(has_clip[2])):
        pytest.skip(
            "no clip at track %d slot 0 — stop_all_clips test needs "
            "a fireable clip at (%d, 0). Add a clip and re-run."
            % (TRACK_ID, TRACK_ID)
        )

    # Save + disable quantization so the fire/stop flip immediately.
    original_q = _query_clip_trigger_quantization(osc)
    osc.send_message("/live/song/set/clip_trigger_quantization", [0])
    wait_one_tick()
    probe_q = _query_clip_trigger_quantization(osc)
    assert probe_q == 0, (
        "couldn't set clip_trigger_quantization=0 for the test — "
        "got %r" % (probe_q,)
    )

    try:
        # Fire the clip and verify it's playing.
        osc.send_message("/live/clip_slot/fire", [TRACK_ID, 0])
        time.sleep(0.3)
        is_playing = osc.query("/live/clip/get/is_playing", [TRACK_ID, 0])
        assert (
            len(is_playing) >= 3
            and bool(is_playing[2]) is True
        ), (
            "clip at (%d, 0) did not start playing after fire — got %r"
            % (TRACK_ID, is_playing)
        )

        # Act: track.stop_all_clips for TRACK_ID.
        osc.send_message("/live/track/stop_all_clips", [TRACK_ID])
        time.sleep(0.3)

        # Verify via read-back.
        is_playing_after = osc.query(
            "/live/clip/get/is_playing", [TRACK_ID, 0],
        )
        assert (
            len(is_playing_after) >= 3
            and bool(is_playing_after[2]) is False
        ), (
            "track.stop_all_clips did not stop the clip at (%d, 0) — "
            "is_playing still %r"
            % (TRACK_ID, is_playing_after)
        )
    finally:
        # Belt-and-suspenders: stop anything that might still be
        # firing (song-wide) before restoring quantization.
        osc.send_message("/live/song/stop_all_clips", [])
        time.sleep(0.2)
        osc.send_message(
            "/live/song/set/clip_trigger_quantization", [original_q],
        )
        wait_one_tick()
        restored = _query_clip_trigger_quantization(osc)
        assert restored == original_q, (
            "failed to restore clip_trigger_quantization — "
            "expected %r, got %r" % (original_q, restored)
        )


# --------------------------------------------------------------------------
# Delete device + restore via browser/load
# --------------------------------------------------------------------------
def _device_names_on_track(osc, track_id):
    """Return the list of device names on a track (excluding the
    leading track-id echo)."""
    reply = osc.query("/live/track/get/devices/name", [track_id])
    assert len(reply) >= 1
    assert int(reply[0]) == track_id
    return [str(n) for n in reply[1:]]


def test_track_delete_device_then_restore(osc):
    """Delete the last device on track 0 and restore it via
    /live/browser/load. Every mutation verified via read-back.

    Restoration is only safe if the existing device was a stock
    Ableton instrument/effect (Operator, Reverb, etc.) — VST/AU
    plugins carry state we can't faithfully recreate, so skip if the
    last device's name isn't in the stock-instrument allow-list.
    Prefer to skip than to leave the user's session mutated.
    """
    _require_midi_track_0(osc)

    before = _device_names_on_track(osc, TRACK_ID)
    if len(before) == 0:
        pytest.skip(
            "track %d has no devices — delete_device test needs at "
            "least one existing device to exercise the endpoint."
            % TRACK_ID
        )

    # Stock Ableton instruments we're confident we can reload by name
    # via /live/browser/load under the "instruments" category. Keep
    # this list conservative: anything not here is skipped.
    # (Devices also live under "audio_effects" and "midi_effects" —
    # for audit simplicity this test only restores instruments. An
    # audio-effect / midi-effect variant can be added later.)
    RESTORABLE_INSTRUMENTS = {
        "Operator", "Analog", "Simpler", "Sampler", "Wavetable",
        "Collision", "Electric", "Drum Rack", "Impulse", "Tension",
        "External Instrument",
    }
    last_name = before[-1]
    if last_name not in RESTORABLE_INSTRUMENTS:
        pytest.skip(
            "last device on track %d is %r — not in the safe "
            "restore allow-list. Skipping to avoid leaving the "
            "session mutated if the test fails mid-way."
            % (TRACK_ID, last_name)
        )

    last_index = len(before) - 1

    # Act: delete the last device.
    osc.send_message("/live/track/delete_device", [TRACK_ID, last_index])
    wait_one_tick()

    after_delete = _device_names_on_track(osc, TRACK_ID)
    assert len(after_delete) == len(before) - 1, (
        "delete_device did not reduce device count — "
        "expected %d, got %d" % (len(before) - 1, len(after_delete))
    )
    assert after_delete == before[:-1], (
        "delete_device removed the wrong device — "
        "expected %r, got %r" % (before[:-1], after_delete)
    )

    # Restore via browser/load.
    reply = osc.query(
        "/live/browser/load",
        [TRACK_ID, "instruments", last_name],
    )
    assert len(reply) >= 1, "browser/load reply was empty: %r" % (reply,)
    assert reply[-1] == "ok", (
        "browser/load did not return ok — got %r" % (reply,)
    )
    wait_one_tick()

    restored = _device_names_on_track(osc, TRACK_ID)
    assert len(restored) == len(before), (
        "browser/load did not restore device count — "
        "expected %d, got %d" % (len(before), len(restored))
    )
    assert restored[-1] == last_name, (
        "restored last device name doesn't match original — "
        "expected %r, got %r" % (last_name, restored[-1])
    )


# --------------------------------------------------------------------------
# /live/track/load/device — not registered in the Bridge
# --------------------------------------------------------------------------
def test_track_load_device_then_delete(osc):
    """/live/track/load/device is NOT a registered endpoint in the
    Bridge — abletonosc/track.py exposes only delete_device and
    stop_all_clips as methods, and no browser-load-by-track shortcut
    exists. Device loading is done via /live/browser/load (see
    test_integration_browser.py::test_browser_load_instrument_then_read_back).

    Skip with a clear reason so the audit trail is explicit: if Ohmic
    ever adds a track.load_device call site, this endpoint will need
    to be registered and this test will need to be implemented.
    """
    pytest.skip(
        "/live/track/load/device not registered in the Bridge — "
        "use /live/browser/load [track, category, name] instead. "
        "Confirmed against abletonosc/track.py: only delete_device "
        "and stop_all_clips are registered as track methods."
    )
