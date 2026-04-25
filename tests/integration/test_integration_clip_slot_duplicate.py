"""Integration tests for the clip_slot_duplicate capability bucket."""
import pytest
from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration

SRC_SLOT = 0
DST_SLOT = 1


def test_duplicate_clip_to_creates_second_clip_with_same_notes(
    osc, temp_midi_track,
):
    track_id = temp_midi_track
    osc.send_message("/live/clip_slot/create_clip", [track_id, SRC_SLOT, 4.0])
    wait_one_tick()
    osc.send_message("/live/clip/add/notes",
        [track_id, SRC_SLOT, 60, 0.0, 1.0, 100, 0, 1.0])
    wait_one_tick()
    osc.send_message("/live/clip_slot/duplicate_clip_to",
        [track_id, SRC_SLOT, track_id, DST_SLOT])
    wait_one_tick()
    src = osc.query("/live/clip/get/notes", [track_id, SRC_SLOT])
    dst = osc.query("/live/clip/get/notes", [track_id, DST_SLOT])
    # Skip echoed indices; note data should match byte-for-byte
    assert src[2:] == dst[2:]
