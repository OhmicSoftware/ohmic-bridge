"""Integration tests for the browser capability bucket.

do not parallelize — the load/delete-device test creates a temporary
MIDI track and mutates its device chain. Running it concurrently with
another test that creates/deletes tracks will thrash the track index
counters."""
import pytest

from tests.integration.conftest import (
    create_temp_midi_track,
    delete_track_by_index,
    find_loadable_instrument,
    wait_one_tick,
)

pytestmark = pytest.mark.integration


# Browser-load tests create their own empty MIDI track at the tail end
# of the session so we never replace an existing instrument on the
# user's project. Ableton's browser.load_item replaces the currently
# loaded instrument when the selected track already has one — on an
# empty track it appends, which is the behavior we want to verify.


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


def test_browser_load_instrument_then_read_back(osc):
    """Create a temporary MIDI track at index -1, load a stock
    instrument (Operator if available, else whatever find_loadable_
    instrument returns for the current Live install) onto it via
    /live/browser/load, verify the track's device list is exactly
    [<instrument>], then delete the temporary track and verify the
    total track count is restored. Every mutation is followed by a
    read-back.

    Wire format (see browser.py load_item):
    input params: (track_index, category, item_name_or_path)
    reply: (track_index, category, item_name_or_path, "ok") on success.

    The test owns all of its state — no preconditions on the user's
    session beyond a running Live install with a non-empty instruments
    category. A user with Live Intro (no Suite content) still passes
    because find_loadable_instrument falls back to whichever instrument
    the browser actually reports.
    """
    original_track_names = osc.query("/live/song/get/track_names", [])
    original_track_count = len(original_track_names)
    original_names_tuple = tuple(str(n) for n in original_track_names)

    # Pick an instrument the current Live install can load.
    instrument_name = find_loadable_instrument(osc)
    assert instrument_name, (
        "find_loadable_instrument returned an empty string — "
        "cannot proceed with browser/load verification"
    )

    # Create a temporary MIDI track at the tail. The new track may
    # come pre-populated with default devices from the user's Live
    # template (Ableton's "New MIDI Track" honors the default MIDI
    # track template), so we record the baseline rather than asserting
    # emptiness.
    target_track = create_temp_midi_track(osc)
    try:
        before_names = _device_names_on_track(osc, target_track)
        before_count = len(before_names)

        # Act: load the instrument. browser.load_item inserts the
        # instrument at the head of the MIDI signal chain (or replaces
        # the existing instrument if one is already present). Either
        # way the device count grows by exactly one UNLESS the track
        # already has an instrument, in which case it replaces it.
        reply = osc.query(
            "/live/browser/load",
            [target_track, "instruments", instrument_name],
        )
        assert len(reply) >= 1, "load reply was empty: %r" % (reply,)
        last = reply[-1]
        # The handler returns a "warning:..." tuple if device count
        # didn't increase (see browser.py:322). For the read-back we
        # require a clean "ok" — if we get a warning instead, fail
        # with the full reply so the developer can see why.
        assert last == "ok", (
            "browser/load did not return ok for %r — got %r"
            % (instrument_name, reply)
        )
        assert int(reply[0]) == target_track, (
            "browser/load echoed wrong track_index — expected %d, got %r"
            % (target_track, reply)
        )
        assert reply[1] == "instruments"
        assert reply[2] == instrument_name
        wait_one_tick()

        # Verify via read-back: the track gained at least one device
        # whose name matches the requested instrument (exact match or
        # substring — Live sometimes wraps the loaded device in a rack
        # with a slightly different display name).
        after_names = _device_names_on_track(osc, target_track)
        assert len(after_names) >= before_count + 1, (
            "expected device count to grow after load — before=%d (%r), "
            "after=%d (%r)"
            % (before_count, before_names, len(after_names), after_names)
        )
        # Identify which device(s) are new (set difference rather than
        # index-based because Live may insert the instrument at the
        # head of the chain, not the tail).
        new_devices = [
            name for name in after_names if name not in before_names
        ] or [after_names[-1]]
        # One of the new devices must match the requested instrument
        # name (exact match or substring in either direction).
        matched = False
        target = instrument_name.lower()
        for name in new_devices:
            lname = name.lower()
            if lname == target or target in lname or lname in target:
                matched = True
                break
        assert matched, (
            "none of the new devices %r match requested instrument %r"
            % (new_devices, instrument_name)
        )
    finally:
        # Teardown: delete the temporary track and verify count and
        # names are back to the original.
        delete_track_by_index(osc, target_track)
        final = osc.query("/live/song/get/track_names", [])
        assert len(final) == original_track_count, (
            "track count not restored after teardown — "
            "expected %d, got %d"
            % (original_track_count, len(final))
        )
        assert tuple(str(n) for n in final) == original_names_tuple, (
            "track names not restored after teardown — "
            "expected %r, got %r"
            % (original_names_tuple, tuple(str(n) for n in final))
        )
