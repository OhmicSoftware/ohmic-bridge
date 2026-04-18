"""Integration tests for the arrangement_clips capability bucket.

do not parallelize — all integration tests target the same Ableton
process and share TRACK_ID = 0."""
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


def _decode_arrangement_notes(reply):
    """Decode /live/arrangement_clip/get/notes reply into per-note dicts.

    Wire format (matches clip.py's arrangement_clip_get_notes handler):
    (track, clip_index, pitch, start, dur, vel, mute, prob, ...).
    Mirrors the session-clip decoder in test_integration_clip_notes.py."""
    assert len(reply) >= 2, (
        "reply must echo (track, clip_index) — got %r" % (reply,)
    )
    payload = reply[2:]
    assert len(payload) % 6 == 0, (
        "note payload length must be a multiple of 6 — got %d entries: %r"
        % (len(payload), reply)
    )
    notes = []
    for offset in range(0, len(payload), 6):
        chunk = payload[offset:offset + 6]
        notes.append({
            "pitch": int(chunk[0]),
            "start": float(chunk[1]),
            "duration": float(chunk[2]),
            "velocity": int(chunk[3]),
            "mute": int(chunk[4]),
            "probability": float(chunk[5]),
        })
    return notes


def test_remove_arrangement_clip_notes(osc):
    """Create an arrangement clip, add two notes one at a time (each
    verified by read-back), remove the first by covering it with a
    pitch/time range, and verify the second survives byte-for-byte
    while the first is gone. Teardown deletes the arrangement clip
    and read-checks that the track has no arrangement clips left."""
    CLIP_INDEX = 0

    # Arrange: create an arrangement clip. A pre-existing arrangement
    # clip on this track would shift the index; delete first to be
    # deterministic.
    # (create writes are followed by a read-back on
    # /live/track/get/arrangement_clips/name.)
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, 0.0, 4.0])
    wait_one_tick()
    names_after_create = osc.query(
        "/live/track/get/arrangement_clips/name", [TRACK_ID],
    )
    # Reply shape: (track, name_0, name_1, ...). At least one name
    # entry after create.
    assert len(names_after_create) >= 2, (
        "expected at least one arrangement clip after create — got %r"
        % (names_after_create,)
    )

    try:
        # Act: add note 1 (C3), verify presence.
        osc.send_message("/live/arrangement_clip/add/notes",
            [TRACK_ID, CLIP_INDEX, 60, 0.0, 0.5, 100, 0, 1.0])
        wait_one_tick()
        reply_after_1 = osc.query(
            "/live/arrangement_clip/get/notes", [TRACK_ID, CLIP_INDEX],
        )
        notes_1 = _decode_arrangement_notes(reply_after_1)
        assert len(notes_1) == 1
        assert notes_1[0]["pitch"] == 60
        assert notes_1[0]["start"] == pytest.approx(0.0)
        assert notes_1[0]["duration"] == pytest.approx(0.5)
        assert notes_1[0]["velocity"] == 100

        # Act: add note 2 (D3) at time 2.0, verify presence.
        osc.send_message("/live/arrangement_clip/add/notes",
            [TRACK_ID, CLIP_INDEX, 62, 2.0, 0.5, 110, 0, 1.0])
        wait_one_tick()
        reply_after_2 = osc.query(
            "/live/arrangement_clip/get/notes", [TRACK_ID, CLIP_INDEX],
        )
        notes_2 = _decode_arrangement_notes(reply_after_2)
        assert len(notes_2) == 2
        by_pitch = {n["pitch"]: n for n in notes_2}
        assert 60 in by_pitch and 62 in by_pitch
        assert by_pitch[62]["start"] == pytest.approx(2.0)
        assert by_pitch[62]["velocity"] == 110

        # Act: remove note 1 only by covering its pitch and time range.
        # Wire: (track, clip_index, pitch_start, pitch_span, time_start, time_span).
        # pitch 60 only (span=1), beats 0.0..1.0 (span=1.0) — leaves
        # pitch 62 at time 2.0 untouched.
        osc.send_message(
            "/live/arrangement_clip/remove/notes",
            [TRACK_ID, CLIP_INDEX, 60, 1, 0.0, 1.0],
        )
        wait_one_tick()

        # Verify: only the D3 note at time 2.0 remains, byte-for-byte.
        reply_after_remove = osc.query(
            "/live/arrangement_clip/get/notes", [TRACK_ID, CLIP_INDEX],
        )
        notes_after_remove = _decode_arrangement_notes(reply_after_remove)
        assert len(notes_after_remove) == 1, (
            "expected exactly 1 note to survive — got %d: %r"
            % (len(notes_after_remove), notes_after_remove)
        )
        survivor = notes_after_remove[0]
        assert survivor["pitch"] == 62
        assert survivor["start"] == pytest.approx(2.0)
        assert survivor["duration"] == pytest.approx(0.5)
        assert survivor["velocity"] == 110
        assert survivor["mute"] == 0
        assert survivor["probability"] == pytest.approx(1.0)
    finally:
        # Teardown: delete the arrangement clip, then read back
        # arrangement_clips/name and assert the entry is gone.
        osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, CLIP_INDEX])
        wait_one_tick()
        names_after_delete = osc.query(
            "/live/track/get/arrangement_clips/name", [TRACK_ID],
        )
        # Reply shape: (track, name_0, ...) — after delete there
        # should be no more name entries than before we started (0).
        # A single-entry reply (track,) means "no arrangement clips".
        assert len(names_after_delete) <= 1, (
            "arrangement clip delete did not land — got %r"
            % (names_after_delete,)
        )
