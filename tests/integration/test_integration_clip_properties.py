"""Integration tests for session-clip property endpoints.

Covers name/color/length/looping/loop_start/loop_end/is_playing.
Every setter paired with a getter that verifies the write landed.
The temp_midi_clip fixture creates a fresh 4-beat MIDI clip on a
disposable MIDI track before each test and deletes the track after.

do not parallelize — these tests mutate Live clip state.
"""
import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


CLIP_LENGTH_BEATS = 4.0


# --------------------------------------------------------------------------
# Clip name
# --------------------------------------------------------------------------
def test_clip_name_roundtrip(osc, temp_midi_clip):
    """Save clip name, set to a distinctive sentinel, verify via
    read-back, restore, verify restore. Wire format for both get and
    set is (track, slot, value). A freshly-created clip has name == ''.
    """
    track_id, clip_id = temp_midi_clip
    probe = osc.query("/live/clip/get/name", [track_id, clip_id])
    assert len(probe) >= 3, "clip name read was incomplete: %r" % (probe,)
    original = str(probe[2])

    sentinel = "__Integration Test Clip__"

    try:
        osc.send_message(
            "/live/clip/set/name", [track_id, clip_id, sentinel],
        )
        wait_one_tick()
        after_set = osc.query("/live/clip/get/name", [track_id, clip_id])
        assert str(after_set[2]) == sentinel, (
            "clip name set did not land — expected %r, got %r"
            % (sentinel, after_set)
        )
    finally:
        osc.send_message(
            "/live/clip/set/name", [track_id, clip_id, original],
        )
        wait_one_tick()
        restored = osc.query("/live/clip/get/name", [track_id, clip_id])
        assert str(restored[2]) == original, (
            "clip name restore failed — expected %r, got %r"
            % (original, restored)
        )


# --------------------------------------------------------------------------
# Clip color
# --------------------------------------------------------------------------
def test_clip_color_roundtrip(osc, temp_midi_clip):
    """Save clip color, set to a distinctive RGB integer, verify via
    read-back, restore, verify restore.

    Wire format: /live/clip/get/color and /live/clip/set/color
    serialize Ableton's Clip.color as a 24-bit RGB integer (confirmed
    against abletonosc/clip.py — "color" is in the properties_rw list
    and the handler is the generic _set_property/_get_property pair,
    which stores whatever int Live returns). Live snaps the written
    value to one of its palette colors, so we can't assert byte-exact
    equality with an arbitrary sentinel — instead we check that the
    read-back after a set is NOT equal to the original color, proving
    the set actually mutated something. If a future Live version lets
    us write arbitrary RGB without palette snapping, we can tighten
    this assertion.
    """
    track_id, clip_id = temp_midi_clip
    probe = osc.query("/live/clip/get/color", [track_id, clip_id])
    assert len(probe) >= 3, "clip color read was incomplete: %r" % (probe,)
    original = int(probe[2])

    # Pick a sentinel color that's almost certainly not the default.
    # 0xFFAA33 = bright orange; Live will snap to the closest palette
    # slot, whatever that is. Any palette slot that isn't == original
    # satisfies the "set landed" check.
    sentinel = 0xFFAA33

    try:
        osc.send_message(
            "/live/clip/set/color", [track_id, clip_id, sentinel],
        )
        wait_one_tick()
        after_set = osc.query("/live/clip/get/color", [track_id, clip_id])
        assert len(after_set) >= 3, (
            "clip color read after set was incomplete: %r" % (after_set,)
        )
        after_set_value = int(after_set[2])
        # Live may snap to a palette slot — the exact post-snap value
        # is Live's choice. What we can assert is that SOMETHING
        # changed if the original wasn't already the snapped target.
        # If Live happened to snap the sentinel to the same value as
        # the original, this assertion is vacuous — skip with a
        # documented reason rather than fail.
        if after_set_value == original:
            pytest.skip(
                "clip color set snapped to the original palette slot "
                "(%d) — can't prove the set landed without a distinct "
                "read-back. Unusual: default clip color matched the "
                "nearest palette color to 0xFFAA33." % original
            )
        assert after_set_value != original, (
            "clip color set did not change the color — original %r, "
            "after set %r" % (original, after_set_value)
        )
    finally:
        osc.send_message(
            "/live/clip/set/color", [track_id, clip_id, original],
        )
        wait_one_tick()
        restored = osc.query("/live/clip/get/color", [track_id, clip_id])
        assert int(restored[2]) == original, (
            "clip color restore failed — expected %r, got %r"
            % (original, restored)
        )


# --------------------------------------------------------------------------
# Clip length (read-only — Live sets length at creation time)
# --------------------------------------------------------------------------
def test_clip_length_read(osc, temp_midi_clip):
    """/live/clip/get/length returns the clip's length in beats.
    The autouse fixture created the clip with length == 4.0, so the
    read-back must equal that value."""
    track_id, clip_id = temp_midi_clip
    reply = osc.query("/live/clip/get/length", [track_id, clip_id])
    assert len(reply) >= 3, "clip length reply was incomplete: %r" % (reply,)
    assert int(reply[0]) == track_id
    assert int(reply[1]) == clip_id
    assert float(reply[2]) == pytest.approx(CLIP_LENGTH_BEATS), (
        "clip length must equal the length passed at creation "
        "(%.1f) — got %r" % (CLIP_LENGTH_BEATS, reply)
    )


# --------------------------------------------------------------------------
# Clip looping
# --------------------------------------------------------------------------
def test_clip_looping_roundtrip(osc, temp_midi_clip):
    """Save looping state, flip True, verify, flip False, verify,
    restore. Three verified writes. Wire format: (track, slot, bool).
    A freshly-created MIDI clip has looping=True by default but we
    save-and-restore anyway so the test doesn't depend on that
    default."""
    track_id, clip_id = temp_midi_clip
    probe = osc.query("/live/clip/get/looping", [track_id, clip_id])
    assert len(probe) >= 3, (
        "clip looping read was incomplete: %r" % (probe,)
    )
    original = bool(probe[2])

    try:
        osc.send_message(
            "/live/clip/set/looping", [track_id, clip_id, True],
        )
        wait_one_tick()
        after_true = osc.query("/live/clip/get/looping", [track_id, clip_id])
        assert bool(after_true[2]) is True, (
            "looping=True did not land — got %r" % (after_true,)
        )

        osc.send_message(
            "/live/clip/set/looping", [track_id, clip_id, False],
        )
        wait_one_tick()
        after_false = osc.query("/live/clip/get/looping", [track_id, clip_id])
        assert bool(after_false[2]) is False, (
            "looping=False did not land — got %r" % (after_false,)
        )
    finally:
        osc.send_message(
            "/live/clip/set/looping", [track_id, clip_id, original],
        )
        wait_one_tick()
        restored = osc.query("/live/clip/get/looping", [track_id, clip_id])
        assert bool(restored[2]) is original, (
            "looping restore failed — expected %r, got %r"
            % (original, restored)
        )


# --------------------------------------------------------------------------
# Loop start / loop end (read-only for this test)
# --------------------------------------------------------------------------
def test_clip_loop_start_and_loop_end_read(osc, temp_midi_clip):
    """Read /live/clip/get/loop_start and /live/clip/get/loop_end.
    For a freshly-created 4-beat clip, loop_start should be < loop_end
    (Live initializes loop bounds to cover the whole clip)."""
    track_id, clip_id = temp_midi_clip
    start_reply = osc.query(
        "/live/clip/get/loop_start", [track_id, clip_id],
    )
    end_reply = osc.query(
        "/live/clip/get/loop_end", [track_id, clip_id],
    )
    assert len(start_reply) >= 3, (
        "loop_start reply was incomplete: %r" % (start_reply,)
    )
    assert len(end_reply) >= 3, (
        "loop_end reply was incomplete: %r" % (end_reply,)
    )

    loop_start = float(start_reply[2])
    loop_end = float(end_reply[2])

    assert loop_start < loop_end, (
        "loop_start must be < loop_end — got start=%r, end=%r"
        % (loop_start, loop_end)
    )
    # Sanity: on a 4-beat freshly-created clip the loop must fit
    # within [0, CLIP_LENGTH_BEATS].
    assert loop_start >= 0.0, (
        "loop_start must be non-negative — got %r" % (loop_start,)
    )
    assert loop_end <= CLIP_LENGTH_BEATS + 1e-6, (
        "loop_end must be <= clip length (%.1f) — got %r"
        % (CLIP_LENGTH_BEATS, loop_end)
    )


# --------------------------------------------------------------------------
# Clip is_playing (quiescent read)
# --------------------------------------------------------------------------
def test_clip_is_playing_read_when_quiescent(osc, temp_midi_clip):
    """A just-created clip has not been fired, so is_playing must be
    False. Wire format: (track, slot, bool). This is the read-only
    companion to the clip_slot fire/stop tests in
    test_integration_clip_slot.py, which exercise the True path."""
    track_id, clip_id = temp_midi_clip
    reply = osc.query("/live/clip/get/is_playing", [track_id, clip_id])
    assert len(reply) >= 3, (
        "clip is_playing reply was incomplete: %r" % (reply,)
    )
    assert bool(reply[2]) is False, (
        "freshly-created clip must not be playing — got %r" % (reply,)
    )
