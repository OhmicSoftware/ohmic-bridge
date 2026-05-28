"""Integration tests for track property endpoints.

Covers name/mute round-trip, has_midi_input, devices/name, delete_device,
and track.stop_all_clips. Tests create disposable MIDI tracks instead of
depending on the user's project layout.

Device loading is done via /live/browser/load, covered by
test_integration_browser.py::test_browser_load_instrument_then_read_back.

do not parallelize — these tests mutate Live track, clip, transport,
and device-chain state.
"""
import pytest

from tests.integration.conftest import (
    create_temp_midi_track,
    delete_track_by_index,
    find_loadable_instrument,
    wait_one_tick,
)

pytestmark = pytest.mark.integration

# --------------------------------------------------------------------------
# Track name round-trip
# --------------------------------------------------------------------------
def test_track_name_roundtrip(osc, temp_midi_track):
    """Save track name, set to a distinctive sentinel, verify via
    read-back, restore, verify restore."""
    track_id = temp_midi_track
    probe = osc.query("/live/track/get/name", [track_id])
    assert len(probe) >= 2, "track name read was incomplete: %r" % (probe,)
    original = str(probe[1])
    sentinel = "__Integration Test Track__"

    try:
        osc.send_message("/live/track/set/name", [track_id, sentinel])
        wait_one_tick()
        after_set = osc.query("/live/track/get/name", [track_id])
        assert str(after_set[1]) == sentinel, (
            "track name set did not land — expected %r, got %r"
            % (sentinel, after_set)
        )
    finally:
        osc.send_message("/live/track/set/name", [track_id, original])
        wait_one_tick()
        restored = osc.query("/live/track/get/name", [track_id])
        assert str(restored[1]) == original, (
            "track name restore failed — expected %r, got %r"
            % (original, restored)
        )


# --------------------------------------------------------------------------
# Track mute round-trip
# --------------------------------------------------------------------------
def test_track_mute_roundtrip(osc, temp_midi_track):
    """Save mute state, flip to True, verify, flip to False, verify,
    restore to original, verify. Three verified writes."""
    track_id = temp_midi_track
    probe = osc.query("/live/track/get/mute", [track_id])
    assert len(probe) >= 2, "track mute read was incomplete: %r" % (probe,)
    # Live returns bools; some OSC wire encodings surface them as 0/1.
    # Normalize to bool for comparison.
    original = bool(probe[1])

    try:
        osc.send_message("/live/track/set/mute", [track_id, True])
        wait_one_tick()
        after_true = osc.query("/live/track/get/mute", [track_id])
        assert bool(after_true[1]) is True, (
            "mute=True did not land — got %r" % (after_true,)
        )

        osc.send_message("/live/track/set/mute", [track_id, False])
        wait_one_tick()
        after_false = osc.query("/live/track/get/mute", [track_id])
        assert bool(after_false[1]) is False, (
            "mute=False did not land — got %r" % (after_false,)
        )
    finally:
        osc.send_message("/live/track/set/mute", [track_id, original])
        wait_one_tick()
        restored = osc.query("/live/track/get/mute", [track_id])
        assert bool(restored[1]) is original, (
            "mute restore failed — expected %r, got %r"
            % (original, restored)
        )


# --------------------------------------------------------------------------
# Read-only track properties
# --------------------------------------------------------------------------
def test_track_has_midi_input_read(osc, temp_midi_track):
    """/live/track/get/has_midi_input returns (track_id, bool). Pure
    read; no mutation."""
    track_id = temp_midi_track
    reply = osc.query("/live/track/get/has_midi_input", [track_id])
    assert len(reply) >= 2, (
        "has_midi_input reply was incomplete: %r" % (reply,)
    )
    assert int(reply[0]) == track_id
    # Live returns a native bool, but some encodings surface 0/1 ints.
    assert isinstance(reply[1], (bool, int)), (
        "has_midi_input value must be bool/int — got %r (type %s)"
        % (reply[1], type(reply[1]).__name__)
    )


def test_track_devices_name_read(osc, temp_midi_track):
    """/live/track/get/devices/name returns (track_id, name_0, ...).
    On a track with no devices, the tuple is length 1 (just track_id).
    """
    track_id = temp_midi_track
    reply = osc.query("/live/track/get/devices/name", [track_id])
    assert len(reply) >= 1, (
        "devices/name reply was empty: %r" % (reply,)
    )
    assert int(reply[0]) == track_id, (
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
    """Create a temporary MIDI track at index -1, create a clip with
    a note in slot 0 of that track, fire the clip, verify
    is_playing=True, call /live/track/stop_all_clips on the track,
    verify is_playing=False. Teardown (verified step by step): stop
    transport, delete clip, delete track, restore quantization.

    Manages its own quantization save/restore with verified read-back
    so this test file doesn't need to depend on another file's
    fixture. Track 0 is never touched.
    """
    import time

    original_track_count = len(osc.query("/live/song/get/track_names", []))

    # Save + disable quantization so fire/stop flip immediately.
    original_q = _query_clip_trigger_quantization(osc)
    osc.send_message("/live/song/set/clip_trigger_quantization", [0])
    wait_one_tick()
    probe_q = _query_clip_trigger_quantization(osc)
    assert probe_q == 0, (
        "couldn't set clip_trigger_quantization=0 for the test — "
        "got %r" % (probe_q,)
    )

    track_idx = None
    try:
        track_idx = create_temp_midi_track(osc)
        slot_idx = 0

        # Arrange: create clip + verify has_clip=True.
        osc.send_message(
            "/live/clip_slot/create_clip", [track_idx, slot_idx, 4.0],
        )
        wait_one_tick()
        has_clip = osc.query(
            "/live/clip_slot/get/has_clip", [track_idx, slot_idx],
        )
        assert len(has_clip) >= 3 and bool(has_clip[2]) is True, (
            "create_clip did not land — has_clip reply %r" % (has_clip,)
        )

        # Arrange: add a note.
        osc.send_message(
            "/live/clip/add/notes",
            [track_idx, slot_idx, 60, 0.0, 1.0, 100, 0, 1.0],
        )
        wait_one_tick()
        notes_reply = osc.query(
            "/live/clip/get/notes", [track_idx, slot_idx],
        )
        assert len(notes_reply) >= 8, (
            "add/notes did not land — got %r" % (notes_reply,)
        )

        # Fire the clip + verify is_playing=True.
        osc.send_message("/live/clip_slot/fire", [track_idx, slot_idx])
        time.sleep(0.3)
        is_playing = osc.query(
            "/live/clip/get/is_playing", [track_idx, slot_idx],
        )
        assert (
            len(is_playing) >= 3
            and bool(is_playing[2]) is True
        ), (
            "clip at (%d, %d) did not start playing after fire — got %r"
            % (track_idx, slot_idx, is_playing)
        )

        # Act: track.stop_all_clips on the temp track.
        osc.send_message("/live/track/stop_all_clips", [track_idx])
        time.sleep(0.3)

        # Verify via read-back.
        is_playing_after = osc.query(
            "/live/clip/get/is_playing", [track_idx, slot_idx],
        )
        assert (
            len(is_playing_after) >= 3
            and bool(is_playing_after[2]) is False
        ), (
            "track.stop_all_clips did not stop the clip at (%d, %d) — "
            "is_playing still %r"
            % (track_idx, slot_idx, is_playing_after)
        )
    finally:
        # Teardown: belt-and-suspenders song-wide stop + verify
        # transport quiescent.
        osc.send_message("/live/song/stop_all_clips", [])
        time.sleep(0.2)
        osc.send_message("/live/song/stop_playing", [])
        time.sleep(0.2)
        song_playing = osc.query("/live/song/get/is_playing", [])
        assert bool(song_playing[0]) is False, (
            "cleanup stop_playing failed — song transport still "
            "playing"
        )

        # Teardown: delete clip + track (only if we got far enough
        # to create them).
        if track_idx is not None:
            has_clip_probe = osc.query(
                "/live/clip_slot/get/has_clip", [track_idx, 0],
            )
            if len(has_clip_probe) >= 3 and bool(has_clip_probe[2]):
                osc.send_message(
                    "/live/clip_slot/delete_clip", [track_idx, 0],
                )
                wait_one_tick()
                has_after = osc.query(
                    "/live/clip_slot/get/has_clip", [track_idx, 0],
                )
                assert bool(has_after[2]) is False, (
                    "delete_clip teardown did not remove clip — "
                    "has_clip still %r" % (has_after,)
                )
            delete_track_by_index(osc, track_idx)
            final_count = len(osc.query("/live/song/get/track_names", []))
            assert final_count == original_track_count, (
                "track count not restored after teardown — "
                "expected %d, got %d"
                % (original_track_count, final_count)
            )

        # Teardown: restore quantization + verify.
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


def _find_browser_match(osc, category, query):
    reply = osc.query("/live/browser/search", [category, query])
    if len(reply) < 3:
        return None
    matches = [str(match) for match in reply[2:] if str(match) != "no matches"]
    if not matches:
        return None
    needle = query.lower()
    for match in matches:
        if needle in match.lower():
            return match
    return matches[0]


def _clear_track_devices(osc, track_id):
    while True:
        names = _device_names_on_track(osc, track_id)
        if not names:
            return
        ack = osc.query("/live/track/delete_device", [track_id, 0])
        assert ack == (track_id, 0, "ok"), (
            "delete_device during cleanup returned %r" % (ack,)
        )
        wait_one_tick()


def test_track_move_device_reorders_loaded_plugins_without_reload(osc):
    """Move an existing plugin instance and verify order by read-back."""
    pro_q = _find_browser_match(osc, "plugins", "Pro-Q 4")
    span = _find_browser_match(osc, "plugins", "SPAN")
    if not pro_q or not span:
        pytest.skip("Pro-Q 4 and SPAN plugins are required for this move test")

    track_idx = create_temp_midi_track(osc)
    try:
        _clear_track_devices(osc, track_idx)
        _load_browser_device(osc, track_idx, "plugins", pro_q)
        loaded = _load_browser_device(osc, track_idx, "plugins", span)

        pro_q_index = _index_matching(loaded, "Pro-Q")
        span_index = _index_matching(loaded, "SPAN")
        assert pro_q_index < span_index, (
            "expected initial Pro-Q before SPAN order, got %r" % (loaded,)
        )

        reply = osc.query(
            "/live/track/move_device",
            [track_idx, span_index, track_idx, pro_q_index],
        )
        assert len(reply) >= 7, "move_device reply was incomplete: %r" % (reply,)
        assert reply[0] == track_idx
        assert reply[1] == span_index
        assert reply[2] == track_idx
        assert reply[3] == pro_q_index
        assert reply[6] == "ok", "move_device did not return ok: %r" % (reply,)
        wait_one_tick()

        moved = _device_names_on_track(osc, track_idx)
        moved_span_index = _index_matching(moved, "SPAN")
        moved_pro_q_index = _index_matching(moved, "Pro-Q")
        assert moved_span_index < moved_pro_q_index, (
            "SPAN was not moved before Pro-Q: before=%r, after=%r"
            % (loaded, moved)
        )
        assert len(moved) == len(loaded), (
            "move_device changed device count: before=%r, after=%r"
            % (loaded, moved)
        )
        assert int(reply[5]) == moved_span_index, (
            "move_device actual index %r did not match read-back %r"
            % (reply[5], moved)
        )
    finally:
        delete_track_by_index(osc, track_idx)
        wait_one_tick()


def _load_browser_device(osc, track_id, category, item):
    reply = osc.query("/live/browser/load", [track_id, category, item])
    assert len(reply) >= 1 and reply[-1] == "ok", (
        "browser/load did not return ok for %r in %s - got %r"
        % (item, category, reply)
    )
    wait_one_tick()
    return _device_names_on_track(osc, track_id)


def _index_matching(names, needle):
    needle = needle.lower()
    for index, name in enumerate(names):
        if needle in name.lower():
            return index
    raise AssertionError("could not find %r in device names %r" % (needle, names))


def test_track_delete_device_then_restore(osc):
    """Create a temporary MIDI track at index -1, load a stock
    instrument onto it via /live/browser/load, verify the device is
    present, delete the device via /live/track/delete_device, verify
    absence, reload the same instrument, verify presence, then delete
    the temporary track. Track 0 is never touched.

    Every mutation verified via read-back. find_loadable_instrument
    picks whichever stock instrument the current Live install has
    (Operator on Suite, else the first-available instrument from the
    browser) so the test works on any license tier.
    """
    original_track_count = len(osc.query("/live/song/get/track_names", []))
    original_track_names = tuple(
        str(n) for n in osc.query("/live/song/get/track_names", [])
    )

    instrument_name = find_loadable_instrument(osc)
    assert instrument_name, (
        "find_loadable_instrument returned an empty string — cannot "
        "proceed with delete_device/restore verification"
    )

    track_idx = create_temp_midi_track(osc)
    try:
        # Record the baseline devices on the new track (Ableton
        # templates may pre-populate new MIDI tracks).
        before_baseline = _device_names_on_track(osc, track_idx)
        baseline_count = len(before_baseline)

        # Load the instrument + verify presence.
        load_reply = osc.query(
            "/live/browser/load",
            [track_idx, "instruments", instrument_name],
        )
        assert len(load_reply) >= 1 and load_reply[-1] == "ok", (
            "initial browser/load did not return ok for %r — got %r"
            % (instrument_name, load_reply)
        )
        wait_one_tick()

        after_first_load = _device_names_on_track(osc, track_idx)
        assert len(after_first_load) >= baseline_count + 1, (
            "device count did not grow after initial load — "
            "baseline=%d (%r), after=%d (%r)"
            % (baseline_count, before_baseline,
               len(after_first_load), after_first_load)
        )
        # Identify the newly-loaded device by set difference. Live
        # may insert it at the head of the chain, not the tail.
        new_devices_first = [
            name for name in after_first_load
            if name not in before_baseline
        ] or [after_first_load[-1]]
        # Find one new device whose name matches (exact or substring)
        # the requested instrument — that's the one we'll delete.
        target = instrument_name.lower()
        loaded_device_name = None
        loaded_device_index = None
        for idx, name in enumerate(after_first_load):
            if name in before_baseline:
                continue
            lname = name.lower()
            if lname == target or target in lname or lname in target:
                loaded_device_name = name
                loaded_device_index = idx
                break
        # Fallback: use the first new device.
        if loaded_device_name is None:
            loaded_device_name = new_devices_first[0]
            loaded_device_index = after_first_load.index(loaded_device_name)

        # Act: delete the loaded device.
        ack = osc.query(
            "/live/track/delete_device", [track_idx, loaded_device_index],
        )
        assert ack == (track_idx, loaded_device_index, "ok"), (
            "delete_device must ack (track, device_index, 'ok') - got %r" % (ack,)
        )
        wait_one_tick()

        after_delete = _device_names_on_track(osc, track_idx)
        assert len(after_delete) == len(after_first_load) - 1, (
            "delete_device did not reduce device count — "
            "expected %d, got %d (%r)"
            % (len(after_first_load) - 1, len(after_delete), after_delete)
        )
        # Verify: the deleted device is no longer present (or at
        # least, there's one fewer instance of it).
        assert after_delete.count(loaded_device_name) == (
            after_first_load.count(loaded_device_name) - 1
        ), (
            "delete_device did not remove %r — before=%r, after=%r"
            % (loaded_device_name, after_first_load, after_delete)
        )

        # Act: reload the instrument + verify presence again.
        reload_reply = osc.query(
            "/live/browser/load",
            [track_idx, "instruments", instrument_name],
        )
        assert len(reload_reply) >= 1 and reload_reply[-1] == "ok", (
            "reload browser/load did not return ok — got %r"
            % (reload_reply,)
        )
        wait_one_tick()

        after_reload = _device_names_on_track(osc, track_idx)
        assert len(after_reload) >= len(after_delete) + 1, (
            "device count did not grow after reload — "
            "before=%d (%r), after=%d (%r)"
            % (len(after_delete), after_delete,
               len(after_reload), after_reload)
        )
        # Verify: a device whose name matches the instrument is now
        # present again.
        reload_matched = False
        for name in after_reload:
            lname = name.lower()
            if lname == target or target in lname or lname in target:
                reload_matched = True
                break
        assert reload_matched, (
            "none of the devices in %r match requested instrument %r "
            "after reload"
            % (after_reload, instrument_name)
        )
    finally:
        # Teardown: delete the temporary track + verify count and
        # names restored.
        delete_track_by_index(osc, track_idx)
        final = osc.query("/live/song/get/track_names", [])
        assert len(final) == original_track_count, (
            "track count not restored after teardown — "
            "expected %d, got %d"
            % (original_track_count, len(final))
        )
        assert tuple(str(n) for n in final) == original_track_names, (
            "track names not restored after teardown — "
            "expected %r, got %r"
            % (original_track_names, tuple(str(n) for n in final))
        )


