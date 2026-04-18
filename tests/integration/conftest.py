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


# --------------------------------------------------------------------------
# Shared helpers — tests own their state
# --------------------------------------------------------------------------
# These helpers let tests create the preconditions they need (an empty MIDI
# track, a loadable stock instrument) rather than skipping when the user's
# session lacks the right state. Every mutation is paired with a read-back
# that verifies the write landed; every teardown verifies the restore.
#
# Used by test_integration_browser.py, test_integration_scene.py,
# test_integration_song_transport.py, and test_integration_track.py.


def create_temp_midi_track(osc):
    """Create a MIDI track at index -1 (append) and return the new
    track's index. Asserts via read-back that the track count grew by
    exactly one. Caller is responsible for eventual
    delete_track_by_index() cleanup.

    Raises AssertionError on failure so the test fails loudly rather
    than silently proceeding with a mismatched index."""
    before = osc.query("/live/song/get/track_names", [])
    before_count = len(before)

    osc.send_message("/live/song/create_midi_track", [-1])
    wait_one_tick()

    after = osc.query("/live/song/get/track_names", [])
    after_count = len(after)
    assert after_count == before_count + 1, (
        "create_midi_track did not increment track count — "
        "expected %d, got %d (before=%r, after=%r)"
        % (before_count + 1, after_count, before, after)
    )
    # Ableton appends new tracks at the tail, so the new index is
    # before_count (0-based).
    new_index = before_count
    # Sanity: the new slot has a non-empty name.
    new_name = str(after[new_index])
    assert new_name != "", (
        "newly-created MIDI track has an empty name — %r" % (after,)
    )
    return new_index


def delete_track_by_index(osc, track_index):
    """Delete a track by index via /live/song/delete_track and assert
    via read-back that the track count dropped by exactly one. Intended
    for teardown of tracks created by create_temp_midi_track()."""
    before = osc.query("/live/song/get/track_names", [])
    before_count = len(before)
    assert 0 <= track_index < before_count, (
        "delete_track_by_index called with out-of-range index %d "
        "(track_count=%d)" % (track_index, before_count)
    )

    osc.send_message("/live/song/delete_track", [track_index])
    wait_one_tick()

    after = osc.query("/live/song/get/track_names", [])
    after_count = len(after)
    assert after_count == before_count - 1, (
        "delete_track did not decrement track count — "
        "expected %d, got %d"
        % (before_count - 1, after_count)
    )


# Stock Ableton instruments we'll attempt to load in priority order.
# Operator ships with Live Suite, but Live Intro / Standard licenses
# don't include it — so we fall back through the list and, if none
# match, use whatever the browser reports as the first loadable
# instrument on the user's machine.
_STOCK_INSTRUMENT_PREFERENCE = (
    "Operator",
    "Simpler",
    "Drum Rack",
    "Analog",
    "Sampler",
    "Wavetable",
    "Collision",
    "Electric",
    "Impulse",
    "Tension",
)


def find_loadable_instrument(osc):
    """Return the name of a stock instrument that can be loaded via
    /live/browser/load with the "instruments" category on the current
    Live install. Strategy:

      1. Try each name in the stock preference list via /live/browser/search.
         Return the first one the browser confirms is present.
      2. If none match (Intro license without Suite content), fall
         back to the first result of an unfiltered /live/browser/search
         with an empty query — empty string is contained in every item
         name, so the search returns every loadable instrument.

    Raises AssertionError if no loadable instrument exists in the
    "instruments" category at all (a genuinely broken Live install)."""
    for candidate in _STOCK_INSTRUMENT_PREFERENCE:
        reply = osc.query(
            "/live/browser/search", ["instruments", candidate],
        )
        # Wire: (category, query, match_1, ..., match_n) on success;
        # (category, query, "no matches") on failure.
        if len(reply) < 3:
            continue
        matches = list(reply[2:])
        if matches == ["no matches"]:
            continue
        # Prefer an exact (case-sensitive) name match so we get
        # "Operator" rather than a preset that happens to contain the
        # word.
        if candidate in matches:
            return candidate
        # Fallback: the browser found a match whose display name
        # differs from the query. Use the first result.
        return str(matches[0])

    # No preferred stock instrument matched — widen the search to
    # "anything loadable in instruments". Empty query matches every
    # item because "" is a substring of every string.
    reply = osc.query("/live/browser/search", ["instruments", ""])
    assert len(reply) >= 3, (
        "/live/browser/search with empty query returned too few "
        "results — expected (category, query, *matches), got %r"
        % (reply,)
    )
    matches = list(reply[2:])
    assert matches and matches != ["no matches"], (
        "no loadable instruments found in the Live browser — "
        "cannot proceed with browser/load tests. Reply: %r" % (reply,)
    )
    return str(matches[0])
