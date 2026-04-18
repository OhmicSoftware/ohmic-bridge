"""Integration tests for the clip_slot_duplicate capability bucket."""
import pytest
from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration

TRACK_ID = 0
SRC_SLOT = 0
DST_SLOT = 1


@pytest.fixture(autouse=True)
def _clean_slots(osc):
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, SRC_SLOT])
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, DST_SLOT])
    wait_one_tick()
    yield
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, SRC_SLOT])
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, DST_SLOT])


def test_duplicate_clip_to_creates_second_clip_with_same_notes(osc):
    osc.send_message("/live/clip_slot/create_clip", [TRACK_ID, SRC_SLOT, 4.0])
    wait_one_tick()
    osc.send_message("/live/clip/add/notes",
        [TRACK_ID, SRC_SLOT, 60, 0.0, 1.0, 100, 0, 1.0])
    wait_one_tick()
    osc.send_message("/live/clip_slot/duplicate_clip_to",
        [TRACK_ID, SRC_SLOT, TRACK_ID, DST_SLOT])
    wait_one_tick()
    src = osc.query("/live/clip/get/notes", [TRACK_ID, SRC_SLOT])
    dst = osc.query("/live/clip/get/notes", [TRACK_ID, DST_SLOT])
    # Skip echoed indices; note data should match byte-for-byte
    assert src[2:] == dst[2:]
