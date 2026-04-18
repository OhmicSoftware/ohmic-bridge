"""Integration tests for the arrangement_clips capability bucket."""
import pytest
from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration

TRACK_ID = 0


def test_create_arrangement_clip_then_delete(osc):
    # Create an arrangement clip at time 0 with 4 beats length
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, 0.0, 4.0])
    wait_one_tick()
    # Reading the per-track arrangement_clips listing should show at
    # least one entry. Shape: (track, name_0, name_1, ...).
    names = osc.query("/live/track/get/arrangement_clips/name", [TRACK_ID])
    assert len(names) >= 2  # track id + at least one name
    # Clean up: delete the clip we just created (assume index 0)
    osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, 0])


def test_add_notes_to_arrangement_clip_roundtrips(osc):
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, 0.0, 4.0])
    wait_one_tick()
    osc.send_message("/live/arrangement_clip/add/notes",
        [TRACK_ID, 0, 60, 0.5, 1.0, 100, 0, 1.0])
    wait_one_tick()
    result = osc.query("/live/arrangement_clip/get/notes", [TRACK_ID, 0])
    assert result[2] == 60
    osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, 0])
