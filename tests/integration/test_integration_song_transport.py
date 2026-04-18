"""Integration tests for song transport + song structure endpoints.

Covers play/stop, tempo get/set, is_playing, song name, song time,
clip_trigger_quantization, num_scenes, track_names, create/delete of
MIDI/audio tracks and scenes.

do not parallelize — these tests mutate global song state (tempo,
transport, quantization, track count, scene count). Running them
alongside any other integration test will thrash shared state.
"""
import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


# _quantization_none is promoted to tests/integration/conftest.py so it
# can be shared across song_transport, clip_slot, and scene fire tests.
# Any test in this file that needs it can depend on it by argument name —
# pytest resolves the fixture from conftest.py automatically.


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def _restore_scene_count(osc):
    """Capture the initial scene count. On teardown, if the test leaked
    extra scenes (because it raised before cleanup), delete the tail
    scenes with verified read-back until count matches original.
    Defends the next test from a dirty session."""
    probe = osc.query("/live/song/get/num_scenes", [])
    assert len(probe) >= 1, "num_scenes reply was empty: %r" % (probe,)
    original_count = int(probe[0])

    yield original_count

    current = osc.query("/live/song/get/num_scenes", [])
    current_count = int(current[0])
    # Delete excess scenes from the tail, verifying each delete landed.
    while current_count > original_count:
        # Delete the last scene (scene index current_count - 1).
        osc.send_message("/live/song/delete_scene", [current_count - 1])
        wait_one_tick()
        after = osc.query("/live/song/get/num_scenes", [])
        after_count = int(after[0])
        assert after_count == current_count - 1, (
            "delete_scene did not reduce num_scenes — expected %d, got %d"
            % (current_count - 1, after_count)
        )
        current_count = after_count


@pytest.fixture
def _restore_track_count(osc):
    """Capture the initial track names + count. On teardown, if the
    test leaked extra tracks, delete them from the tail with verified
    read-back."""
    probe = osc.query("/live/song/get/track_names", [])
    original_names = tuple(str(n) for n in probe)
    original_count = len(original_names)

    yield original_names

    current = osc.query("/live/song/get/track_names", [])
    current_count = len(current)
    while current_count > original_count:
        # Delete the last track (index current_count - 1).
        osc.send_message("/live/song/delete_track", [current_count - 1])
        wait_one_tick()
        after = osc.query("/live/song/get/track_names", [])
        after_count = len(after)
        assert after_count == current_count - 1, (
            "delete_track did not reduce track count — expected %d, got %d"
            % (current_count - 1, after_count)
        )
        current_count = after_count


# --------------------------------------------------------------------------
# Tempo
# --------------------------------------------------------------------------
def test_tempo_roundtrip(osc):
    """Save tempo, set to a distinctive value, verify, restore,
    verify restore. Every write paired with a read."""
    probe = osc.query("/live/song/get/tempo", [])
    assert len(probe) >= 1, "tempo read returned empty: %r" % (probe,)
    original = float(probe[0])

    try:
        target = 137.5
        osc.send_message("/live/song/set/tempo", [target])
        wait_one_tick()
        after_set = osc.query("/live/song/get/tempo", [])
        assert after_set[0] == pytest.approx(target), (
            "tempo set did not land — expected %r, got %r"
            % (target, after_set)
        )
    finally:
        osc.send_message("/live/song/set/tempo", [original])
        wait_one_tick()
        restored = osc.query("/live/song/get/tempo", [])
        assert restored[0] == pytest.approx(original), (
            "tempo restore failed — expected %r, got %r"
            % (original, restored)
        )


# --------------------------------------------------------------------------
# Transport play/stop
# --------------------------------------------------------------------------
def _query_is_playing(osc):
    reply = osc.query("/live/song/get/is_playing", [])
    assert len(reply) >= 1, "is_playing reply was empty: %r" % (reply,)
    return bool(reply[0])


def test_is_playing_starts_false_when_quiescent(osc, _quantization_none):
    """Precondition for the start/stop transport tests: at the moment
    this test runs, transport is stopped. If the developer left the
    project mid-playback, skip rather than misreport."""
    if _query_is_playing(osc):
        pytest.skip(
            "Ableton is currently playing — transport tests require "
            "a quiescent project. Stop playback and re-run."
        )
    # Read-only assertion: we didn't write anything, but we did
    # verify the observable state matches the precondition.
    assert _query_is_playing(osc) is False


def test_start_playing_flips_is_playing_true(osc, _quantization_none):
    """With quantization=None, /live/song/start_playing must flip
    /live/song/get/is_playing to True on the next tick."""
    # Arrange: make sure we start stopped so the flip is observable.
    if _query_is_playing(osc):
        osc.send_message("/live/song/stop_playing", [])
        wait_one_tick()
        wait_one_tick()
        assert _query_is_playing(osc) is False, (
            "couldn't stop playback before starting test"
        )

    try:
        osc.send_message("/live/song/start_playing", [])
        # A single tick is often too fast; give transport up to ~0.3s
        # to flip. (Unlike set_* of scalar properties, transport
        # changes cross a Live scheduler boundary.)
        import time
        time.sleep(0.3)
        assert _query_is_playing(osc) is True, (
            "is_playing did not flip True after start_playing"
        )
    finally:
        # Cleanup: stop playback so subsequent tests start quiescent.
        osc.send_message("/live/song/stop_playing", [])
        import time
        time.sleep(0.3)
        # Verified cleanup: confirm transport stopped.
        assert _query_is_playing(osc) is False, (
            "cleanup stop_playing failed — leaving session playing"
        )


def test_stop_playing_flips_is_playing_false(osc, _quantization_none):
    """With quantization=None, /live/song/stop_playing after
    /live/song/start_playing must flip is_playing to False."""
    import time

    # Arrange: make sure playback is running.
    osc.send_message("/live/song/start_playing", [])
    time.sleep(0.3)
    # Minimal precondition read — if start didn't take, the main
    # assertion below is meaningless. Fail fast with a clear message.
    assert _query_is_playing(osc) is True, (
        "couldn't start playback for stop_playing test — "
        "is_playing still False after start_playing"
    )

    # Act + assert: stop and verify via read-back.
    osc.send_message("/live/song/stop_playing", [])
    time.sleep(0.3)
    assert _query_is_playing(osc) is False, (
        "is_playing did not flip False after stop_playing"
    )


def test_stop_all_clips_stops_firing_clip(osc, _quantization_none):
    """Fire the clip at (0, 0), verify it's playing, send
    /live/song/stop_all_clips, verify it's no longer playing.
    Requires a clip at (0, 0) — skip if slot empty."""
    import time

    # Precondition: a clip exists at (0, 0).
    has_clip = osc.query("/live/clip_slot/get/has_clip", [0, 0])
    if not (len(has_clip) >= 3 and bool(has_clip[2])):
        pytest.skip(
            "no clip at track 0 slot 0 — stop_all_clips test needs "
            "a fireable clip at (0, 0). Add a clip to that slot and "
            "re-run."
        )

    try:
        # Fire the clip.
        osc.send_message("/live/clip_slot/fire", [0, 0])
        time.sleep(0.3)
        is_playing = osc.query("/live/clip/get/is_playing", [0, 0])
        assert len(is_playing) >= 3 and bool(is_playing[2]) is True, (
            "clip at (0, 0) did not start playing after fire — "
            "got %r" % (is_playing,)
        )

        # Act: stop_all_clips.
        osc.send_message("/live/song/stop_all_clips", [])
        time.sleep(0.3)

        # Verify via read-back.
        is_playing_after = osc.query("/live/clip/get/is_playing", [0, 0])
        assert (
            len(is_playing_after) >= 3
            and bool(is_playing_after[2]) is False
        ), (
            "stop_all_clips did not stop the clip at (0, 0) — "
            "is_playing still %r" % (is_playing_after,)
        )
    finally:
        # Belt-and-suspenders: make sure nothing's still firing.
        osc.send_message("/live/song/stop_all_clips", [])
        time.sleep(0.2)


# --------------------------------------------------------------------------
# Read-only song properties
# --------------------------------------------------------------------------
def test_song_name_read(osc):
    """/live/song/get/name is read-only — Live's Song.name is derived
    from the saved file name. For an unsaved Live set the property
    returns an empty string; for a saved set it returns the file name
    without the .als extension. The endpoint must always return a
    string (empty or populated) and never raise."""
    reply = osc.query("/live/song/get/name", [])
    assert len(reply) >= 1, "song name reply was empty: %r" % (reply,)
    name = reply[0]
    assert isinstance(name, str), (
        "expected song name to be a string, got %r (type %s)"
        % (name, type(name).__name__)
    )


def test_current_song_time_read(osc):
    """/live/song/get/current_song_time returns the current playback
    position in beats (float). Read-only from the integration suite's
    perspective — we don't attempt to rewind a developer's session."""
    reply = osc.query("/live/song/get/current_song_time", [])
    assert len(reply) >= 1, (
        "current_song_time reply was empty: %r" % (reply,)
    )
    value = reply[0]
    assert isinstance(value, (int, float)), (
        "expected numeric current_song_time, got %r (type %s)"
        % (value, type(value).__name__)
    )
    assert float(value) >= 0.0, (
        "current_song_time must be non-negative — got %r" % (value,)
    )


def test_clip_trigger_quantization_read(osc):
    """/live/song/get/clip_trigger_quantization returns the current
    Live.Song.Quantization enum as an int. Independent of the
    _quantization_none fixture — this test verifies the bare read."""
    reply = osc.query("/live/song/get/clip_trigger_quantization", [])
    assert len(reply) >= 1, (
        "clip_trigger_quantization reply was empty: %r" % (reply,)
    )
    value = reply[0]
    # Live's Quantization enum values are ints 0..13.
    assert isinstance(value, int), (
        "expected int clip_trigger_quantization, got %r (type %s)"
        % (value, type(value).__name__)
    )
    assert 0 <= int(value) <= 20, (
        "clip_trigger_quantization out of expected enum range: %r"
        % (value,)
    )


# --------------------------------------------------------------------------
# Song structure — scenes
# --------------------------------------------------------------------------
def test_num_scenes_read_reflects_creation(osc, _restore_scene_count):
    """Create a scene at index -1 (append), verify num_scenes
    incremented by 1, delete the newly-created scene, verify count
    returns to original."""
    original_count = _restore_scene_count

    # Act: create scene at index -1 (Ableton appends at tail).
    osc.send_message("/live/song/create_scene", [-1])
    wait_one_tick()

    after_create = osc.query("/live/song/get/num_scenes", [])
    assert len(after_create) >= 1
    after_create_count = int(after_create[0])
    assert after_create_count == original_count + 1, (
        "create_scene did not increment num_scenes — "
        "expected %d, got %d"
        % (original_count + 1, after_create_count)
    )

    # The new scene is at index original_count (0-based).
    new_scene_index = original_count
    osc.send_message("/live/song/delete_scene", [new_scene_index])
    wait_one_tick()

    after_delete = osc.query("/live/song/get/num_scenes", [])
    after_delete_count = int(after_delete[0])
    assert after_delete_count == original_count, (
        "delete_scene did not restore num_scenes — "
        "expected %d, got %d"
        % (original_count, after_delete_count)
    )


# --------------------------------------------------------------------------
# Song structure — tracks
# --------------------------------------------------------------------------
def test_track_names_read(osc):
    """/live/song/get/track_names returns a flat tuple of strings.
    Ableton always has at least one track per session (you can't
    delete the last track), so the tuple is non-empty."""
    reply = osc.query("/live/song/get/track_names", [])
    assert len(reply) >= 1, "track_names must be non-empty"
    for name in reply:
        assert isinstance(name, str), (
            "every track name must be a string — got %r (type %s)"
            % (name, type(name).__name__)
        )


def test_create_midi_track_then_delete(osc, _restore_track_count):
    """Create a MIDI track at index -1, verify track count +1 and the
    new track name is present, delete at the new index, verify count
    and names restored."""
    original_names = _restore_track_count
    original_count = len(original_names)

    # Act: create MIDI track at index -1 (append).
    osc.send_message("/live/song/create_midi_track", [-1])
    wait_one_tick()

    after_create = osc.query("/live/song/get/track_names", [])
    after_create_count = len(after_create)
    assert after_create_count == original_count + 1, (
        "create_midi_track did not increment track count — "
        "expected %d, got %d"
        % (original_count + 1, after_create_count)
    )
    new_track_index = original_count
    new_track_name = str(after_create[new_track_index])
    # Ableton names new MIDI tracks "N MIDI" by default but we can't
    # depend on the exact naming — just confirm a new entry landed.
    assert new_track_name != "", (
        "new MIDI track has an empty name — %r" % (after_create,)
    )

    # Act: delete the newly-created track.
    osc.send_message("/live/song/delete_track", [new_track_index])
    wait_one_tick()

    after_delete = osc.query("/live/song/get/track_names", [])
    after_delete_count = len(after_delete)
    assert after_delete_count == original_count, (
        "delete_track did not restore track count — "
        "expected %d, got %d"
        % (original_count, after_delete_count)
    )
    restored_names = tuple(str(n) for n in after_delete)
    assert restored_names == original_names, (
        "delete_track left track list in an unexpected state — "
        "expected %r, got %r" % (original_names, restored_names)
    )


def test_create_audio_track_then_delete(osc, _restore_track_count):
    """Create an audio track at index -1, verify count +1 and new name
    present, delete, verify restore. Mirror of the MIDI track test."""
    original_names = _restore_track_count
    original_count = len(original_names)

    osc.send_message("/live/song/create_audio_track", [-1])
    wait_one_tick()

    after_create = osc.query("/live/song/get/track_names", [])
    after_create_count = len(after_create)
    assert after_create_count == original_count + 1, (
        "create_audio_track did not increment track count — "
        "expected %d, got %d"
        % (original_count + 1, after_create_count)
    )
    new_track_index = original_count
    new_track_name = str(after_create[new_track_index])
    assert new_track_name != "", (
        "new audio track has an empty name — %r" % (after_create,)
    )

    osc.send_message("/live/song/delete_track", [new_track_index])
    wait_one_tick()

    after_delete = osc.query("/live/song/get/track_names", [])
    after_delete_count = len(after_delete)
    assert after_delete_count == original_count, (
        "delete_track did not restore track count — "
        "expected %d, got %d"
        % (original_count, after_delete_count)
    )
    restored_names = tuple(str(n) for n in after_delete)
    assert restored_names == original_names, (
        "delete_track left track list in an unexpected state — "
        "expected %r, got %r" % (original_names, restored_names)
    )
