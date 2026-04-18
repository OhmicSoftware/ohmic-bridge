"""Integration tests for clip_slot endpoints.

Covers has_clip read, clip_slot/fire -> clip.is_playing=True, and
clip_slot/stop -> clip.is_playing=False. All tests require
quantization=None so the fire/stop flip immediately — shared via the
_quantization_none fixture in conftest.py.

do not parallelize — these tests mutate the clip at (0, 0) and the
song-wide clip_trigger_quantization. Running alongside any other test
that touches that slot or transport state will thrash.
"""
import time

import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


TRACK_ID = 0
CLIP_ID = 0
CLIP_LENGTH_BEATS = 4.0


@pytest.fixture(autouse=True)
def _fresh_clip(osc):
    """Guarantee a clean 4-beat MIDI clip at (TRACK_ID, CLIP_ID) before
    each test, and delete it after."""
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, CLIP_ID])
    wait_one_tick()
    osc.send_message(
        "/live/clip_slot/create_clip",
        [TRACK_ID, CLIP_ID, CLIP_LENGTH_BEATS],
    )
    wait_one_tick()
    yield
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, CLIP_ID])


def _query_has_clip(osc):
    """Read /live/clip_slot/get/has_clip at (TRACK_ID, CLIP_ID) and
    return a normalized bool. Wire format: (track, slot, bool)."""
    reply = osc.query(
        "/live/clip_slot/get/has_clip", [TRACK_ID, CLIP_ID],
    )
    assert len(reply) >= 3, (
        "has_clip reply was incomplete: %r" % (reply,)
    )
    assert int(reply[0]) == TRACK_ID
    assert int(reply[1]) == CLIP_ID
    return bool(reply[2])


def _query_clip_is_playing(osc):
    """Read /live/clip/get/is_playing at (TRACK_ID, CLIP_ID) and
    return a normalized bool. Wire format: (track, slot, bool)."""
    reply = osc.query(
        "/live/clip/get/is_playing", [TRACK_ID, CLIP_ID],
    )
    assert len(reply) >= 3, (
        "clip is_playing reply was incomplete: %r" % (reply,)
    )
    return bool(reply[2])


# --------------------------------------------------------------------------
# has_clip
# --------------------------------------------------------------------------
def test_clip_slot_has_clip_read(osc):
    """The autouse fixture just created a clip, so has_clip must be
    True. Then delete the clip via /live/clip_slot/delete_clip and
    verify has_clip flipped to False. The fixture's teardown
    re-deletes (no-op on an already-empty slot) so no extra cleanup
    needed here.

    Covers /live/clip_slot/get/has_clip and /live/clip_slot/delete_clip
    in a single test — every write verified by a read-back."""
    # Arrange is done by the autouse fixture — prove the clip exists.
    assert _query_has_clip(osc) is True, (
        "autouse fixture did not create a clip at (%d, %d) — "
        "has_clip returned False"
        % (TRACK_ID, CLIP_ID)
    )

    # Act: delete the clip.
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, CLIP_ID])
    wait_one_tick()

    # Verify: has_clip flipped to False.
    assert _query_has_clip(osc) is False, (
        "clip_slot/delete_clip did not remove the clip — "
        "has_clip still True"
    )

    # The fixture teardown will call delete_clip again (harmless no-op).
    # No need to recreate — the next test's fixture will set up its own.


# --------------------------------------------------------------------------
# clip_slot/fire
# --------------------------------------------------------------------------
def test_clip_slot_fire_flips_clip_is_playing_true(osc, _quantization_none):
    """Add a note to the clip (so it's not empty — firing an empty
    clip is a no-op in Live's session view), fire the clip_slot, wait,
    verify is_playing=True. Teardown stops the clip via
    /live/clip_slot/stop with verified read-back.

    Requires _quantization_none so the fire takes effect immediately
    instead of waiting for the next quantized beat."""
    # Arrange: add a note, verify it landed.
    osc.send_message(
        "/live/clip/add/notes",
        [TRACK_ID, CLIP_ID, 60, 0.0, 1.0, 100, 0, 1.0],
    )
    wait_one_tick()
    notes_reply = osc.query("/live/clip/get/notes", [TRACK_ID, CLIP_ID])
    # Wire: (track, slot, pitch, start, dur, vel, mute, prob) -> 8 entries
    assert len(notes_reply) >= 8, (
        "clip must have at least 1 note before fire — got %r"
        % (notes_reply,)
    )
    assert int(notes_reply[2]) == 60, (
        "expected pitch-60 note in clip — got %r" % (notes_reply,)
    )

    # Sanity: clip isn't already playing.
    assert _query_clip_is_playing(osc) is False, (
        "precondition failed — clip was already playing before fire"
    )

    try:
        # Act: fire the clip slot.
        osc.send_message("/live/clip_slot/fire", [TRACK_ID, CLIP_ID])
        # Fire crosses a Live scheduler boundary — one tick is often
        # too fast. 0.3s matches the pattern used by
        # test_start_playing_flips_is_playing_true.
        time.sleep(0.3)

        # Verify via read-back.
        assert _query_clip_is_playing(osc) is True, (
            "clip_slot/fire did not flip clip.is_playing to True"
        )
    finally:
        # Cleanup: stop the clip via clip_slot/stop, verify it stopped.
        # Also stop song transport — firing a clip starts Live's
        # song-level transport, and clip_slot/stop only halts the clip
        # not the transport. Leaving transport playing would make the
        # next test's quiescent-precondition check skip.
        osc.send_message("/live/clip_slot/stop", [TRACK_ID, CLIP_ID])
        time.sleep(0.3)
        assert _query_clip_is_playing(osc) is False, (
            "cleanup stop failed — clip still playing after "
            "clip_slot/stop"
        )
        osc.send_message("/live/song/stop_playing", [])
        time.sleep(0.2)
        song_playing = osc.query("/live/song/get/is_playing", [])
        assert bool(song_playing[0]) is False, (
            "cleanup stop_playing failed — song transport still "
            "playing after teardown"
        )


# --------------------------------------------------------------------------
# clip_slot/stop
# --------------------------------------------------------------------------
def test_clip_slot_stop_flips_clip_is_playing_false(osc, _quantization_none):
    """Fire the clip (with a note so Live actually plays it), verify
    it's playing, send /live/clip_slot/stop, verify is_playing flipped
    False. Mirror of the fire test but asserts the stop direction."""
    # Arrange: add a note, verify it landed.
    osc.send_message(
        "/live/clip/add/notes",
        [TRACK_ID, CLIP_ID, 60, 0.0, 1.0, 100, 0, 1.0],
    )
    wait_one_tick()
    notes_reply = osc.query("/live/clip/get/notes", [TRACK_ID, CLIP_ID])
    assert len(notes_reply) >= 8, (
        "clip must have at least 1 note before fire — got %r"
        % (notes_reply,)
    )

    # Fire the clip and confirm it's playing (precondition for the
    # stop assertion to be meaningful).
    osc.send_message("/live/clip_slot/fire", [TRACK_ID, CLIP_ID])
    time.sleep(0.3)
    assert _query_clip_is_playing(osc) is True, (
        "precondition failed — clip didn't start playing after fire, "
        "so the stop test would be vacuous"
    )

    try:
        # Act: stop via clip_slot/stop.
        osc.send_message("/live/clip_slot/stop", [TRACK_ID, CLIP_ID])
        time.sleep(0.3)

        # Verify via read-back.
        assert _query_clip_is_playing(osc) is False, (
            "clip_slot/stop did not flip clip.is_playing to False"
        )
    finally:
        # Cleanup: stop song transport — firing the clip started
        # Live's song transport, and clip_slot/stop halts the clip
        # without stopping transport. Leaving transport playing would
        # cause the next test's quiescent precondition to skip.
        osc.send_message("/live/song/stop_playing", [])
        time.sleep(0.2)
        song_playing = osc.query("/live/song/get/is_playing", [])
        assert bool(song_playing[0]) is False, (
            "cleanup stop_playing failed — song transport still "
            "playing after teardown"
        )
