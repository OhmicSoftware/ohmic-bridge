"""Integration tests for /live/view/* endpoints.

Covers the selected_clip round-trip: /live/view/set/selected_clip
writes (track, slot) to Live's View, /live/view/get/selected_clip
reads it back. Wire format for the getter is a flat (track_idx,
slot_idx) tuple — confirmed against abletonosc/view.py.

The temp_midi_clip fixture creates a clip on a disposable MIDI track
before each test so /live/view/set/selected_clip has something to select.

do not parallelize — touches the global Live view state and the clip
selection.
"""
import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


def test_view_selected_clip_roundtrip(osc, temp_midi_clip):
    """Set the selected clip to the temp clip, verify via
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
    track_id, clip_id = temp_midi_clip
    # Act: select the clip.
    osc.send_message(
        "/live/view/set/selected_clip", [track_id, clip_id],
    )
    wait_one_tick()

    # Verify via read-back.
    reply = osc.query("/live/view/get/selected_clip", [])
    assert len(reply) == 2, (
        "view/get/selected_clip must return a (track, slot) 2-tuple "
        "— got %r" % (reply,)
    )
    assert int(reply[0]) == track_id, (
        "selected track mismatch after set — expected %d, got %r"
        % (track_id, reply)
    )
    assert int(reply[1]) == clip_id, (
        "selected slot mismatch after set — expected %d, got %r"
        % (clip_id, reply)
    )
