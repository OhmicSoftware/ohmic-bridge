"""Integration tests for scene endpoints.

Covers scene tempo/time-signature round-trips (capability buckets
scene_tempo + scene_time_signature), plus scene name round-trip and
scene/fire -> clip.is_playing verification. Every setter paired with
a getter that verifies the write landed.

do not parallelize — these tests mutate scene 0's tempo, time sig,
and name, and may fire clips on other tracks. Running alongside any
other scene- or transport-touching test will thrash state.
"""
import time

import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration

SCENE_ID = 0


@pytest.fixture
def _ensure_two_scenes(osc):
    """Guarantee the session has at least 2 scenes for tests that
    need scene indices 0 and 1 (e.g. to fire scene 0 without assuming
    it's the only scene). Restore the original scene count on
    teardown — any scenes created by this fixture are deleted with
    verified read-back.

    Live sessions always have at least one scene, so the minimum
    work here is 0 or 1 create+delete pair."""
    probe = osc.query("/live/song/get/num_scenes", [])
    assert len(probe) >= 1, (
        "num_scenes reply was empty: %r" % (probe,)
    )
    original_count = int(probe[0])

    # Create scenes until we have at least 2.
    scenes_added = 0
    while True:
        cur = osc.query("/live/song/get/num_scenes", [])
        cur_count = int(cur[0])
        if cur_count >= 2:
            break
        osc.send_message("/live/song/create_scene", [-1])
        wait_one_tick()
        after = osc.query("/live/song/get/num_scenes", [])
        after_count = int(after[0])
        assert after_count == cur_count + 1, (
            "create_scene did not increment count — expected %d, "
            "got %d" % (cur_count + 1, after_count)
        )
        scenes_added += 1

    yield

    # Teardown: delete any scenes we added, verify each delete landed.
    for _ in range(scenes_added):
        cur = osc.query("/live/song/get/num_scenes", [])
        cur_count = int(cur[0])
        osc.send_message("/live/song/delete_scene", [cur_count - 1])
        wait_one_tick()
        after = osc.query("/live/song/get/num_scenes", [])
        after_count = int(after[0])
        assert after_count == cur_count - 1, (
            "delete_scene during teardown did not decrement count — "
            "expected %d, got %d" % (cur_count - 1, after_count)
        )

    # Final sanity check: count matches original.
    final = osc.query("/live/song/get/num_scenes", [])
    final_count = int(final[0])
    assert final_count == original_count, (
        "scene count not restored after test — original %d, final %d"
        % (original_count, final_count)
    )


# --------------------------------------------------------------------------
# scene_tempo + scene_time_signature (existing tests — unchanged)
# --------------------------------------------------------------------------
def test_scene_tempo_roundtrip(osc):
    osc.send_message("/live/scene/set/tempo", [SCENE_ID, 128.5])
    wait_one_tick()
    reply = osc.query("/live/scene/get/tempo", [SCENE_ID])
    assert reply[0] == SCENE_ID
    assert reply[1] == pytest.approx(128.5)


def test_scene_time_signature_roundtrip(osc):
    osc.send_message("/live/scene/set/time_signature_numerator", [SCENE_ID, 7])
    osc.send_message("/live/scene/set/time_signature_denominator", [SCENE_ID, 8])
    wait_one_tick()
    num = osc.query("/live/scene/get/time_signature_numerator", [SCENE_ID])
    den = osc.query("/live/scene/get/time_signature_denominator", [SCENE_ID])
    assert num[1] == 7
    assert den[1] == 8


# --------------------------------------------------------------------------
# scene name round-trip
# --------------------------------------------------------------------------
def test_scene_set_name_roundtrip(osc, _ensure_two_scenes):
    """Save scene 0's name, set to a distinctive sentinel, verify via
    read-back, restore, verify restore.

    Wire format (from abletonosc/scene.py + _get_property/_set_property):
      get/name(scene_idx)            -> (scene_idx, name_str)
      set/name(scene_idx, name_str)  -> no return
    """
    probe = osc.query("/live/scene/get/name", [SCENE_ID])
    assert len(probe) >= 2, (
        "scene name read was incomplete: %r" % (probe,)
    )
    original = str(probe[1])

    sentinel = "__Integration Test Scene__"

    try:
        osc.send_message("/live/scene/set/name", [SCENE_ID, sentinel])
        wait_one_tick()
        after_set = osc.query("/live/scene/get/name", [SCENE_ID])
        assert str(after_set[1]) == sentinel, (
            "scene name set did not land — expected %r, got %r"
            % (sentinel, after_set)
        )
    finally:
        osc.send_message("/live/scene/set/name", [SCENE_ID, original])
        wait_one_tick()
        restored = osc.query("/live/scene/get/name", [SCENE_ID])
        assert str(restored[1]) == original, (
            "scene name restore failed — expected %r, got %r"
            % (original, restored)
        )


# --------------------------------------------------------------------------
# scene fire
# --------------------------------------------------------------------------
def _any_midi_clip_in_scene(osc, scene_index):
    """Return (track_idx, clip_idx) of the first MIDI clip in the
    given scene, or None if no clips exist in that scene. Walks every
    track's clip_slot at scene_index and queries has_clip."""
    track_names = osc.query("/live/song/get/track_names", [])
    num_tracks = len(track_names)
    for track_idx in range(num_tracks):
        reply = osc.query(
            "/live/clip_slot/get/has_clip", [track_idx, scene_index],
        )
        if len(reply) >= 3 and bool(reply[2]):
            # Confirm it's a MIDI clip (audio clips can't be driven
            # by add/notes — for a fire test we just need something
            # fireable, so has_clip=True is sufficient).
            return (track_idx, scene_index)
    return None


def test_scene_fire_with_clip_fires_clip(osc, _quantization_none,
                                          _ensure_two_scenes):
    """Fire scene 0 via /live/scene/fire, wait for transport flip,
    verify at least one clip in scene 0 is playing via
    /live/clip/get/is_playing. Requires quantization=None so fire
    takes effect immediately.

    If scene 0 is empty (no clips on any track), skip — this is a
    project-setup precondition, not a Bridge failure. The autouse
    fixture ensures >= 2 scenes exist but doesn't guarantee clips
    in them.

    Teardown: stop_all_clips via /live/song/stop_all_clips with
    verified read-back so other tests don't inherit a firing scene.
    """
    target = _any_midi_clip_in_scene(osc, SCENE_ID)
    if target is None:
        pytest.skip(
            "scene %d has no clips on any track — scene/fire test "
            "needs at least one fireable clip in scene %d. Add a "
            "clip and re-run." % (SCENE_ID, SCENE_ID)
        )

    track_idx, slot_idx = target

    # Precondition: clip not currently playing.
    pre = osc.query("/live/clip/get/is_playing", [track_idx, slot_idx])
    assert len(pre) >= 3, (
        "clip is_playing precondition read was incomplete: %r" % (pre,)
    )
    if bool(pre[2]):
        # Stop anything that's already firing so the scene/fire
        # transition is observable. Verify the stop landed.
        osc.send_message("/live/song/stop_all_clips", [])
        time.sleep(0.3)
        pre2 = osc.query(
            "/live/clip/get/is_playing", [track_idx, slot_idx],
        )
        assert bool(pre2[2]) is False, (
            "couldn't stop playback before scene/fire test — got %r"
            % (pre2,)
        )

    try:
        # Act: fire the scene.
        osc.send_message("/live/scene/fire", [SCENE_ID])
        # scene/fire crosses Live's scheduler boundary even with
        # quantization=None — give it 0.3s like the other fire tests.
        time.sleep(0.3)

        # Verify via read-back: the target clip is now playing.
        after = osc.query(
            "/live/clip/get/is_playing", [track_idx, slot_idx],
        )
        assert len(after) >= 3 and bool(after[2]) is True, (
            "scene/fire did not start the clip at (%d, %d) — "
            "is_playing still %r"
            % (track_idx, slot_idx, after)
        )
    finally:
        # Cleanup: stop all clips, verify quiescent. Also stop song
        # transport — scene/fire starts Live's song transport, and
        # stop_all_clips only halts clips without stopping transport.
        osc.send_message("/live/song/stop_all_clips", [])
        time.sleep(0.3)
        final = osc.query(
            "/live/clip/get/is_playing", [track_idx, slot_idx],
        )
        assert bool(final[2]) is False, (
            "cleanup stop_all_clips failed — clip at (%d, %d) "
            "still playing"
            % (track_idx, slot_idx)
        )
        osc.send_message("/live/song/stop_playing", [])
        time.sleep(0.2)
        song_playing = osc.query("/live/song/get/is_playing", [])
        assert bool(song_playing[0]) is False, (
            "cleanup stop_playing failed — song transport still "
            "playing after teardown"
        )
