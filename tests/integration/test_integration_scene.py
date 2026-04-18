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

from tests.integration.conftest import (
    create_temp_midi_track,
    delete_track_by_index,
    wait_one_tick,
)

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
def test_scene_fire_with_clip_fires_clip(osc, _quantization_none,
                                          _ensure_two_scenes):
    """Create a temporary MIDI track, put a MIDI clip with a note in
    slot SCENE_ID on that track, fire scene SCENE_ID via
    /live/scene/fire, and verify the clip's /live/clip/get/is_playing
    flips True. Every mutation paired with a read-back.

    Requires quantization=None so scene/fire takes effect immediately
    instead of waiting for the next quantized boundary.

    Teardown (verified step by step):
      1. stop all clips via /live/song/stop_all_clips + verify clip
         is_playing=False.
      2. stop song transport via /live/song/stop_playing + verify
         song is_playing=False.
      3. delete the temporary clip + verify has_clip=False.
      4. delete the temporary track + verify track count restored.
    """
    original_track_count = len(osc.query("/live/song/get/track_names", []))

    # Arrange: create the temporary track + clip. Fixture helpers
    # assert the mutations landed; we don't need to re-check here.
    track_idx = create_temp_midi_track(osc)
    try:
        slot_idx = SCENE_ID
        osc.send_message(
            "/live/clip_slot/create_clip", [track_idx, slot_idx, 4.0],
        )
        wait_one_tick()
        has_clip = osc.query(
            "/live/clip_slot/get/has_clip", [track_idx, slot_idx],
        )
        assert len(has_clip) >= 3 and bool(has_clip[2]) is True, (
            "create_clip did not land — has_clip still %r" % (has_clip,)
        )

        # Add a note so the clip is non-silent when fired (firing an
        # empty clip does transition clip.is_playing, but making the
        # clip audible helps when diagnosing flaky test runs
        # manually).
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

        # Precondition: clip not currently playing.
        pre = osc.query("/live/clip/get/is_playing", [track_idx, slot_idx])
        assert len(pre) >= 3, (
            "clip is_playing precondition read was incomplete: %r"
            % (pre,)
        )
        assert bool(pre[2]) is False, (
            "freshly-created clip was unexpectedly already playing — "
            "got %r" % (pre,)
        )

        # Act: fire the scene.
        osc.send_message("/live/scene/fire", [SCENE_ID])
        # scene/fire crosses Live's scheduler boundary even with
        # quantization=None — give it 0.3s like the other fire tests.
        time.sleep(0.3)

        # Verify via read-back: the clip in the fired scene is now
        # playing.
        after = osc.query(
            "/live/clip/get/is_playing", [track_idx, slot_idx],
        )
        assert len(after) >= 3 and bool(after[2]) is True, (
            "scene/fire did not start the clip at (%d, %d) — "
            "is_playing still %r"
            % (track_idx, slot_idx, after)
        )
    finally:
        # Teardown step 1: stop all clips + verify quiescent.
        osc.send_message("/live/song/stop_all_clips", [])
        time.sleep(0.3)
        # Only check is_playing if the clip slot still has a clip —
        # if the create_clip failed earlier, has_clip is False and
        # /live/clip/get/is_playing returns nothing useful.
        has_clip_probe = osc.query(
            "/live/clip_slot/get/has_clip", [track_idx, SCENE_ID],
        )
        if len(has_clip_probe) >= 3 and bool(has_clip_probe[2]):
            clip_playing = osc.query(
                "/live/clip/get/is_playing", [track_idx, SCENE_ID],
            )
            assert bool(clip_playing[2]) is False, (
                "cleanup stop_all_clips failed — clip at (%d, %d) "
                "still playing"
                % (track_idx, SCENE_ID)
            )

            # Teardown step 3: delete the clip, verify has_clip=False.
            osc.send_message(
                "/live/clip_slot/delete_clip", [track_idx, SCENE_ID],
            )
            wait_one_tick()
            has_clip_after = osc.query(
                "/live/clip_slot/get/has_clip", [track_idx, SCENE_ID],
            )
            assert bool(has_clip_after[2]) is False, (
                "delete_clip teardown did not remove clip — "
                "has_clip still %r" % (has_clip_after,)
            )

        # Teardown step 2: stop transport + verify song quiescent.
        osc.send_message("/live/song/stop_playing", [])
        time.sleep(0.2)
        song_playing = osc.query("/live/song/get/is_playing", [])
        assert bool(song_playing[0]) is False, (
            "cleanup stop_playing failed — song transport still "
            "playing after teardown"
        )

        # Teardown step 4: delete the temporary track + verify count.
        delete_track_by_index(osc, track_idx)
        final_count = len(osc.query("/live/song/get/track_names", []))
        assert final_count == original_track_count, (
            "track count not restored after teardown — "
            "expected %d, got %d"
            % (original_track_count, final_count)
        )
