"""Integration tests for the browser capability bucket.

do not parallelize — the load/delete-device test mutates track 0's
device chain and running it against another test that touches track 0
will thrash."""
import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


# Browser-load tests target the first empty MIDI track so we don't
# replace an existing instrument on the user's session (Ableton's
# browser.load_item replaces the currently-selected track's instrument
# rather than appending when the track already has one). The test
# probes tracks at query time and skips if none is empty — this is a
# project-setup precondition, not a Bridge failure.


def test_browser_capabilities_returnable(osc):
    """browser/get/capabilities returns a flat tuple of supported
    category names. The handler source (abletonosc/browser.py)
    returns ``tuple(supported)`` where supported is a list of strings
    drawn from CATEGORY_MAP keys, or ``("unsupported",)`` when the
    browser subsystem is absent. Assert every element is one of the
    known strings — no mixed types, no trailing integers."""
    reply = osc.query("/live/browser/get/capabilities", [])
    allowed = {
        "instruments", "audio_effects", "midi_effects",
        "plugins", "user_library", "presets", "unsupported",
    }
    assert len(reply) >= 1, "capabilities reply was empty"
    for category in reply:
        assert isinstance(category, str), (
            "every element must be a string; got %r in %r" % (category, reply)
        )
        assert category in allowed, (
            "unknown category %r in %r — update the allow-list if the "
            "Bridge added a new category" % (category, reply)
        )


def test_browser_get_names_for_instruments(osc):
    reply = osc.query("/live/browser/get/names", ["instruments"])
    # Reply shape: (category_name, name1, name2, ...). Must have at
    # least the echoed category name.
    assert len(reply) >= 1
    assert reply[0] == "instruments"


def _device_names_on_track(osc, track_id):
    """Return the list of device names on a track (excluding the
    leading track-id echo).

    /live/track/get/devices/name reply shape: (track_id, name_0, name_1, ...).
    """
    reply = osc.query("/live/track/get/devices/name", [track_id])
    assert len(reply) >= 1
    assert reply[0] == track_id
    return [str(n) for n in reply[1:]]


def test_browser_search_returns_results(osc):
    """/live/browser/search with a known-present instrument name
    must return at least one match. Operator ships with every
    Ableton Live Suite install since Live 7 and is the most
    defensible baseline across user installs.

    Wire format (see abletonosc/browser.py search_items):
    input params: (category, query)
    reply: (category, query, match_1, match_2, ...) on success,
           (category, query, "no matches") when nothing matches."""
    reply = osc.query("/live/browser/search", ["instruments", "Operator"])
    assert len(reply) >= 3, (
        "search reply must echo (category, query) plus at least one "
        "match/status — got %r" % (reply,)
    )
    assert reply[0] == "instruments"
    # Query is echoed lower-cased by the handler.
    assert reply[1] == "operator"
    matches = list(reply[2:])
    assert matches != ["no matches"], (
        "expected at least one match for 'Operator' in instruments "
        "— browser returned 'no matches'. Is Live Suite content "
        "installed? (Operator is Suite-only on some license tiers)"
    )
    assert "Operator" in matches, (
        "expected 'Operator' among matches — got %r" % (matches,)
    )


def _find_empty_midi_track(osc):
    """Return the index of the first MIDI track with zero devices, or
    None if no such track exists. Browser-load tests need an empty
    track because Ableton's browser.load_item replaces the currently
    loaded instrument when the selected track already has one; that
    would mutate the user's session rather than round-trip cleanly."""
    track_names = osc.query("/live/song/get/track_names", [])
    for i in range(len(track_names)):
        # Must be a MIDI track (browser.load_item targets the
        # selected track's MIDI signal chain for instruments).
        has_midi = osc.query("/live/track/get/has_midi_input", [i])
        # Reply: (track_id, bool). Skip non-MIDI tracks.
        if not (len(has_midi) >= 2 and bool(has_midi[1])):
            continue
        devs = osc.query("/live/track/get/devices/name", [i])
        # Reply: (track_id,) when track has no devices.
        if len(devs) == 1:
            return i
    return None


def test_browser_load_instrument_then_read_back(osc):
    """Load Operator onto an empty MIDI track via /live/browser/load,
    verify the track's device list grew by one and the new device is
    "Operator", then delete the new device and verify the list is
    empty again. Every mutation is followed by a read-back.

    Wire format (see browser.py load_item):
    input params: (track_index, category, item_name_or_path)
    reply: (track_index, category, item_name_or_path, "ok") on success.

    Skips if no empty MIDI track exists — project-setup precondition.
    """
    target_track = _find_empty_midi_track(osc)
    if target_track is None:
        pytest.skip(
            "no empty MIDI track available in the current project "
            "— browser/load would replace an existing instrument "
            "rather than add a new one, so the read-back can't "
            "distinguish the 'loaded' case. Add an empty MIDI "
            "track to the session and re-run."
        )

    before_names = _device_names_on_track(osc, target_track)
    before_count = len(before_names)
    assert before_count == 0, (
        "target track must start empty — got %r" % (before_names,)
    )

    try:
        # Act: load Operator onto the empty track.
        reply = osc.query(
            "/live/browser/load",
            [target_track, "instruments", "Operator"],
        )
        assert len(reply) >= 1, "load reply was empty: %r" % (reply,)
        last = reply[-1]
        assert last == "ok", (
            "browser/load did not return ok — got %r" % (reply,)
        )
        assert reply[0] == target_track
        assert reply[1] == "instruments"
        assert reply[2] == "Operator"
        wait_one_tick()

        # Verify: device count grew by exactly one and the new
        # device is Operator.
        after_names = _device_names_on_track(osc, target_track)
        assert len(after_names) == before_count + 1, (
            "expected device count to grow by 1 — before=%d after=%d "
            "(before=%r, after=%r)"
            % (before_count, len(after_names), before_names, after_names)
        )
        assert after_names[-1] == "Operator", (
            "expected last device to be Operator — got %r"
            % (after_names,)
        )
    finally:
        # Teardown: delete the device we added (last index) and
        # verify count returns to zero.
        probe = _device_names_on_track(osc, target_track)
        if len(probe) > before_count:
            device_index_to_delete = len(probe) - 1
            osc.send_message(
                "/live/track/delete_device",
                [target_track, device_index_to_delete],
            )
            wait_one_tick()
            restored = _device_names_on_track(osc, target_track)
            assert len(restored) == before_count, (
                "delete_device did not restore device count — "
                "expected %d, got %d (%r)"
                % (before_count, len(restored), restored)
            )
            assert restored == before_names, (
                "delete_device left track in an unexpected state — "
                "expected %r, got %r" % (before_names, restored)
            )
