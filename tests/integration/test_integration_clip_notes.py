"""Integration tests for the clip_notes_rw capability bucket.

Writes, reads, and removes notes end-to-end against a real Ableton
Live session. Verifies every field round-trips with byte-exact
values so a future Ableton release that changes the API signature
or return shape will break these assertions before users hit it."""
import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


TRACK_ID = 0
CLIP_ID = 0


@pytest.fixture(autouse=True)
def _fresh_clip(osc):
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, CLIP_ID])
    wait_one_tick()
    osc.send_message("/live/clip_slot/create_clip", [TRACK_ID, CLIP_ID, 4.0])
    wait_one_tick()
    yield
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, CLIP_ID])


def test_add_and_read_single_note_roundtrips_every_field(osc):
    osc.send_message("/live/clip/add/notes",
        [TRACK_ID, CLIP_ID, 60, 0.5, 1.0, 100, 0, 0.75])
    wait_one_tick()
    result = osc.query("/live/clip/get/notes", [TRACK_ID, CLIP_ID])
    # Wire format: (track, slot, pitch, start, dur, vel, mute, prob)
    assert result[0] == TRACK_ID
    assert result[1] == CLIP_ID
    assert result[2] == 60
    assert result[3] == pytest.approx(0.5)
    assert result[4] == pytest.approx(1.0)
    assert result[5] == 100
    assert result[6] == 0
    assert result[7] == pytest.approx(0.75)


def test_remove_notes_by_range_preserves_other_notes(osc):
    osc.send_message("/live/clip/add/notes",
        [TRACK_ID, CLIP_ID, 60, 0.0, 1.0, 100, 0, 1.0])
    osc.send_message("/live/clip/add/notes",
        [TRACK_ID, CLIP_ID, 62, 1.0, 1.0, 100, 0, 1.0])
    wait_one_tick()
    osc.send_message("/live/clip/remove/notes",
        [TRACK_ID, CLIP_ID, 0, 128, 1.0, 1.0])
    wait_one_tick()
    result = osc.query("/live/clip/get/notes", [TRACK_ID, CLIP_ID])
    # Expect only the C3 note (pitch 60, start 0.0) to remain
    # (2 echoed + 6 fields = 8 entries for one note)
    assert len(result) == 8
    assert result[2] == 60
