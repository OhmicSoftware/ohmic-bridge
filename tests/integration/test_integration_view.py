"""Integration tests for /live/view/* endpoints.

Covers the selected_clip round-trip: /live/view/set/selected_clip
writes (track, slot) to Live's View, /live/view/get/selected_clip
reads it back. Wire format for the getter is a flat (track_idx,
slot_idx) tuple — confirmed against abletonosc/view.py.

Autouse fixture creates a clip at (TRACK_ID, CLIP_ID) before each
test so /live/view/set/selected_clip has something to select.

do not parallelize — touches the global Live view state and the clip
at (0, 0).
"""
import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


TRACK_ID = 0
CLIP_ID = 0
CLIP_LENGTH_BEATS = 4.0


@pytest.fixture(autouse=True)
def _fresh_clip(osc):
    """Guarantee a clean 4-beat MIDI clip at (TRACK_ID, CLIP_ID) so
    there's something to select. Delete on teardown."""
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, CLIP_ID])
    wait_one_tick()
    osc.send_message(
        "/live/clip_slot/create_clip",
        [TRACK_ID, CLIP_ID, CLIP_LENGTH_BEATS],
    )
    wait_one_tick()
    yield
    osc.send_message("/live/clip_slot/delete_clip", [TRACK_ID, CLIP_ID])


def test_view_selected_clip_roundtrip(osc):
    """Set the selected clip to (TRACK_ID, CLIP_ID), verify via
    /live/view/get/selected_clip that the reply reflects that slot.

    Wire format (from abletonosc/view.py):
      - set/selected_clip takes (track, slot), no return.
      - get/selected_clip returns (track, slot) — a flat 2-tuple
        derived from view.selected_track + view.selected_scene.

    No restore is needed: selection is user-visible but harmless —
    the next test's autouse fixture resets state via the clip
    delete/create cycle, and any developer running the suite knows
    their Live view cursor may move.
    """
    # Act: select the clip.
    osc.send_message(
        "/live/view/set/selected_clip", [TRACK_ID, CLIP_ID],
    )
    wait_one_tick()

    # Verify via read-back.
    reply = osc.query("/live/view/get/selected_clip", [])
    assert len(reply) == 2, (
        "view/get/selected_clip must return a (track, slot) 2-tuple "
        "— got %r" % (reply,)
    )
    assert int(reply[0]) == TRACK_ID, (
        "selected track mismatch after set — expected %d, got %r"
        % (TRACK_ID, reply)
    )
    assert int(reply[1]) == CLIP_ID, (
        "selected slot mismatch after set — expected %d, got %r"
        % (CLIP_ID, reply)
    )
