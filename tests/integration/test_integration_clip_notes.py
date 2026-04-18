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


def _decode_notes(reply):
    """Decode a /live/clip/get/notes reply into a list of per-note dicts.

    Wire format: (track, slot, pitch, start, dur, vel, mute, prob,
                  pitch, start, dur, vel, mute, prob, ...).
    Returns a list where each entry captures the six fields for one
    note so tests can do set-based / field-wise comparisons rather
    than wrestling with flat-tuple indices."""
    assert len(reply) >= 2, "reply must echo (track, slot) — got %r" % (reply,)
    track_echo, slot_echo = reply[0], reply[1]
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
    return track_echo, slot_echo, notes


def test_remove_notes_by_id(osc):
    """Add three distinct notes one at a time (each followed by a
    get/notes read-back that proves the add landed), then remove one
    note via /live/clip/remove_notes_by_id, and verify the remaining
    two notes are present byte-for-byte while the removed note is gone.

    Bridge wire contract: /live/clip/remove_notes_by_id takes
    (track, slot, *note_ids) where note_ids are Ableton's opaque
    integer MidiNote.note_id values. The Bridge's get/notes handler
    does not echo those IDs, so we can't derive them from reads. Live
    assigns note IDs sequentially as notes are added to a clip — the
    first note added gets a small-integer ID. We probe a small
    contiguous range (1..32) to find whichever ID Live chose for the
    first-added note; a valid ID removes exactly one note, invalid
    IDs are no-ops (a future Bridge change that turns invalid IDs
    into raised exceptions would surface as a test failure, which is
    what we want)."""
    # Arrange: prove the clip exists.
    has_clip = osc.query("/live/clip_slot/get/has_clip", [TRACK_ID, CLIP_ID])
    assert has_clip[-1] is True or has_clip[-1] == 1, (
        "expected has_clip=True on a freshly-created clip — got %r"
        % (has_clip,)
    )

    # Act 1: add 3 distinct notes, each verified by read-back.
    expected_notes = []

    osc.send_message("/live/clip/add/notes",
        [TRACK_ID, CLIP_ID, 60, 0.0, 0.5, 100, 0, 1.0])
    wait_one_tick()
    reply_after_1 = osc.query("/live/clip/get/notes", [TRACK_ID, CLIP_ID])
    _, _, notes_after_1 = _decode_notes(reply_after_1)
    assert len(notes_after_1) == 1, (
        "after first add, expected 1 note — got %d: %r"
        % (len(notes_after_1), notes_after_1)
    )
    expected_notes.append({
        "pitch": 60, "start": 0.0, "duration": 0.5,
        "velocity": 100, "mute": 0, "probability": 1.0,
    })
    for field, value in expected_notes[0].items():
        assert notes_after_1[0][field] == pytest.approx(value), (
            "first note field %r did not round-trip — expected %r, "
            "got %r" % (field, value, notes_after_1[0][field])
        )

    osc.send_message("/live/clip/add/notes",
        [TRACK_ID, CLIP_ID, 62, 1.0, 0.5, 110, 0, 1.0])
    wait_one_tick()
    reply_after_2 = osc.query("/live/clip/get/notes", [TRACK_ID, CLIP_ID])
    _, _, notes_after_2 = _decode_notes(reply_after_2)
    assert len(notes_after_2) == 2, (
        "after second add, expected 2 notes — got %d"
        % len(notes_after_2)
    )
    expected_notes.append({
        "pitch": 62, "start": 1.0, "duration": 0.5,
        "velocity": 110, "mute": 0, "probability": 1.0,
    })

    osc.send_message("/live/clip/add/notes",
        [TRACK_ID, CLIP_ID, 64, 2.0, 0.5, 120, 0, 1.0])
    wait_one_tick()
    reply_after_3 = osc.query("/live/clip/get/notes", [TRACK_ID, CLIP_ID])
    _, _, notes_after_3 = _decode_notes(reply_after_3)
    assert len(notes_after_3) == 3, (
        "after third add, expected 3 notes — got %d"
        % len(notes_after_3)
    )
    expected_notes.append({
        "pitch": 64, "start": 2.0, "duration": 0.5,
        "velocity": 120, "mute": 0, "probability": 1.0,
    })

    # Act 2: probe contiguous candidate IDs to find the one that
    # removes exactly one note. Live's note IDs for a fresh clip are
    # small integers — 1..32 covers every reasonable assignment. The
    # probe is deterministic: at most one of these IDs matches the
    # first-added note.
    removed_pitch = None
    for candidate_id in range(1, 33):
        osc.send_message(
            "/live/clip/remove_notes_by_id",
            [TRACK_ID, CLIP_ID, candidate_id],
        )
        wait_one_tick()
        reply = osc.query("/live/clip/get/notes", [TRACK_ID, CLIP_ID])
        _, _, remaining = _decode_notes(reply)
        if len(remaining) == 2:
            # Found it. Compute which pitch was removed by comparing
            # against the original three we added.
            remaining_pitches = {n["pitch"] for n in remaining}
            original_pitches = {60, 62, 64}
            missing = original_pitches - remaining_pitches
            assert len(missing) == 1, (
                "exactly one pitch must be missing after remove_by_id "
                "— got remaining=%r, missing=%r"
                % (remaining_pitches, missing)
            )
            removed_pitch = next(iter(missing))
            break
        assert len(remaining) == 3, (
            "remove_notes_by_id with invalid ID=%d must be a no-op "
            "but count changed: %d"
            % (candidate_id, len(remaining))
        )

    assert removed_pitch is not None, (
        "no candidate ID in 1..32 removed a note — either Live "
        "changed the ID-assignment strategy, or the Bridge's "
        "remove_notes_by_id handler is broken"
    )

    # Verify: the remaining two notes must match the expected field
    # values byte-for-byte. Build a pitch -> expected mapping and
    # walk the survivors.
    expected_by_pitch = {n["pitch"]: n for n in expected_notes}
    # pop the removed one so we're asserting against the survivors.
    expected_by_pitch.pop(removed_pitch)

    final_reply = osc.query("/live/clip/get/notes", [TRACK_ID, CLIP_ID])
    _, _, final_notes = _decode_notes(final_reply)
    assert len(final_notes) == 2
    for note in final_notes:
        exp = expected_by_pitch.get(note["pitch"])
        assert exp is not None, (
            "unexpected pitch %d in final notes — expected pitches %r"
            % (note["pitch"], set(expected_by_pitch.keys()))
        )
        for field, value in exp.items():
            assert note[field] == pytest.approx(value), (
                "survivor pitch=%d field %r did not match — "
                "expected %r, got %r"
                % (note["pitch"], field, value, note[field])
            )
    assert removed_pitch not in {n["pitch"] for n in final_notes}, (
        "removed pitch %d unexpectedly present in final notes"
        % removed_pitch
    )
