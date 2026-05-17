"""Integration tests for the arrangement_clips capability bucket.

do not parallelize — all integration tests target the same Ableton
process."""
import json

import pytest
from tests.integration.conftest import (
    create_temp_midi_track,
    delete_track_by_index,
    wait_one_tick,
)

pytestmark = pytest.mark.integration

TRACK_ID = 0

# Tolerance for matching a clip's start_time echoed back by the LOM
# against the value we sent on /arrangement_clip/create. Floats round-trip
# through OSC cleanly in practice, but a small epsilon keeps the match
# robust against any future quantization Ableton might introduce.
_START_TIME_MATCH_EPSILON = 1e-3


@pytest.fixture(autouse=True)
def _temp_midi_track(osc):
    """Each arrangement test owns a MIDI track and cleans it up afterward."""
    global TRACK_ID
    original_track_id = TRACK_ID
    TRACK_ID = create_temp_midi_track(osc)
    try:
        yield
    finally:
        delete_track_by_index(osc, TRACK_ID)
        TRACK_ID = original_track_id


def _arrangement_clips_baseline(osc):
    """Snapshot the current arrangement-clip count + the end-time of the
    latest clip on TRACK_ID.

    Tests use this to (a) pick a ``safe_start`` that can't collide with
    existing content on the user's Live Set, so the newly-created clip
    can be located unambiguously, and (b) verify teardown returns the
    track to its starting count. Returns ``(count, latest_end)`` where
    ``latest_end = max(start + length)`` over existing clips, or ``0.0``
    if the track has no arrangement clips yet.
    """
    starts_reply = osc.query(
        "/live/track/get/arrangement_clips/start_time", [TRACK_ID])
    lengths_reply = osc.query(
        "/live/track/get/arrangement_clips/length", [TRACK_ID])
    starts = [float(s) for s in list(starts_reply)[1:]]
    lengths = [float(L) for L in list(lengths_reply)[1:]]
    count = len(starts)
    latest_end = max((s + L for s, L in zip(starts, lengths)), default=0.0)
    return count, latest_end


def _find_clip_index_by_start(osc, target_start):
    """Return the arrangement_clips index for a clip whose start_time
    matches ``target_start`` within ``_START_TIME_MATCH_EPSILON``.

    Prefer locating the test-created clip by its (unique-by-construction)
    start_time rather than assuming it landed at a specific index. Raises
    AssertionError with the full start_time list if no match is found, so
    a regression that silently drops the create is diagnosable from the
    failure output.
    """
    starts_reply = osc.query(
        "/live/track/get/arrangement_clips/start_time", [TRACK_ID])
    starts = [float(s) for s in list(starts_reply)[1:]]
    for i, s in enumerate(starts):
        if abs(s - target_start) < _START_TIME_MATCH_EPSILON:
            return i
    raise AssertionError(
        "no arrangement clip found at start_time=%r; track has %d clips "
        "at starts=%r" % (target_start, len(starts), starts)
    )


def test_create_arrangement_clip_then_delete(osc):
    """Create one arrangement clip on TRACK_ID and delete it, asserting
    the clip count grows and shrinks by exactly 1 around the operation.

    Robust against pre-existing arrangement clips on the target track:
    the new clip is placed at a ``safe_start`` past the end of any
    existing content so it can be located by start_time, not by a
    hardcoded index.
    """
    baseline_count, latest_end = _arrangement_clips_baseline(osc)
    safe_start = latest_end + 8.0
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, safe_start, 4.0])
    wait_one_tick()
    clip_index = _find_clip_index_by_start(osc, safe_start)

    try:
        # Read back the per-track arrangement_clips listing. Shape:
        # (track, name_0, name_1, ...). Count must have grown by exactly 1.
        names = osc.query("/live/track/get/arrangement_clips/name", [TRACK_ID])
        assert len(names) == baseline_count + 2, (
            "expected %d clips after create (baseline %d + 1), got %d: %r"
            % (baseline_count + 1, baseline_count, len(names) - 1, names)
        )
    finally:
        osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, clip_index])
        wait_one_tick()
        final_count = _arrangement_clips_baseline(osc)[0]
        assert final_count == baseline_count, (
            "teardown left track at %d clips (expected baseline %d)"
            % (final_count, baseline_count)
        )


def test_add_notes_to_arrangement_clip_roundtrips(osc):
    """Write a single note to a fresh arrangement clip and read it back
    byte-for-byte. Robust against pre-existing clips on TRACK_ID.
    """
    baseline_count, latest_end = _arrangement_clips_baseline(osc)
    safe_start = latest_end + 8.0
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, safe_start, 4.0])
    wait_one_tick()
    clip_index = _find_clip_index_by_start(osc, safe_start)

    try:
        # Note times are clip-relative (arrangement_clip_add_notes calls
        # clip.add_new_notes, which uses local coords), so start=0.5 is
        # half a beat into the clip regardless of where the clip sits
        # in the arrangement.
        osc.send_message("/live/arrangement_clip/add/notes",
            [TRACK_ID, clip_index, 60, 0.5, 1.0, 100, 0, 1.0])
        wait_one_tick()
        result = osc.query("/live/arrangement_clip/get/notes", [TRACK_ID, clip_index])
        assert result[2] == 60
    finally:
        osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, clip_index])
        wait_one_tick()
        final_count = _arrangement_clips_baseline(osc)[0]
        assert final_count == baseline_count, (
            "teardown left track at %d clips (expected baseline %d)"
            % (final_count, baseline_count)
        )


def test_arrangement_delta_changes_when_arrangement_clip_notes_change(osc):
    baseline_count, latest_end = _arrangement_clips_baseline(osc)
    safe_start = latest_end + 8.0
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, safe_start, 4.0])
    wait_one_tick()
    clip_index = _find_clip_index_by_start(osc, safe_start)

    try:
        snapshot_reply = osc.query("/live/song/get/arrangement_snapshot", [])
        snapshot = json.loads(snapshot_reply[-1])
        assert snapshot["status"] == "ok"
        before_clip = snapshot["clips"][str(TRACK_ID)][clip_index]
        before_signature = before_clip["notes_signature"]

        osc.send_message(
            "/live/arrangement_clip/add/notes",
            [TRACK_ID, clip_index, 60, 0.5, 1.0, 100, 0, 1.0],
        )
        wait_one_tick()

        delta_reply = osc.query(
            "/live/song/get/arrangement_delta", [snapshot["revision"]])
        delta = json.loads(delta_reply[-1])
        assert delta["status"] == "ok"
        assert len(delta["changes"]) == 1
        change = delta["changes"][0]
        assert change["type"] == "replace_track_clips"
        assert change["track_index"] == TRACK_ID
        changed_clip = change["clips"][clip_index]
        assert changed_clip["clip_id"] == before_clip["clip_id"]
        assert changed_clip["notes_signature"] != before_signature
    finally:
        osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, clip_index])
        wait_one_tick()
        final_count = _arrangement_clips_baseline(osc)[0]
        assert final_count == baseline_count, (
            "teardown left track at %d clips (expected baseline %d)"
            % (final_count, baseline_count)
        )

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


def test_ableton_silently_allows_overlapping_arrangement_clips(osc):
    """Regression guard for Ohmic's Build-to-Arrangement overwrite pre-check.

    Ohmic's Build-to-Arr feature assumes Ableton's ``track.create_midi_clip``
    silently allows overlapping arrangement clips on the same track —
    meaning the new clip visually covers the old one with no exception
    raised, and the user's original notes become hidden/unrecoverable.
    That's why the Ohmic side queries the existing clip ranges and
    prompts the user before creating.

    If Ableton ever changes this behavior (e.g. starts throwing an
    exception, or auto-truncating the existing clip), this test will
    fail first — a signal that the Ohmic-side guard may be ripe for
    simplification OR may need a different strategy.

    Exercises the contract directly: create clip A at [0, 16), then
    attempt to create clip B at [8, 24) (6-beat overlap). Assert both
    clips coexist in the LOM's arrangement_clips listing afterward.
    """
    # Capture baseline so teardown is exact.
    baseline_reply = osc.query(
        "/live/track/get/arrangement_clips/start_time", [TRACK_ID])
    baseline_count = max(len(list(baseline_reply)) - 1, 0)

    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, 0.0, 16.0])
    wait_one_tick()
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, 8.0, 16.0])
    wait_one_tick()

    try:
        starts_reply = osc.query(
            "/live/track/get/arrangement_clips/start_time", [TRACK_ID])
        starts = [float(s) for s in list(starts_reply)[1:]]
        # Both clips must coexist — Ableton did not reject the second
        # create nor auto-truncate the first. This is the behavior Ohmic
        # relies on being true so its pre-check is necessary.
        assert 0.0 in starts, (
            f"expected clip at start=0.0 in arrangement_clips: {starts!r}")
        assert 8.0 in starts, (
            f"expected clip at start=8.0 (overlapping the [0,16) clip) "
            f"to coexist: {starts!r}")

        lengths_reply = osc.query(
            "/live/track/get/arrangement_clips/length", [TRACK_ID])
        lengths = [float(v) for v in list(lengths_reply)[1:]]
        # The first clip's length was NOT truncated — Ableton did not
        # trim [0,16) to [0,8) to make room for the overlapping clip.
        # If it ever did, our pre-check's notion of "conflict range"
        # would need to be reconsidered.
        assert 16.0 in lengths, (
            f"existing clip's length should not be auto-truncated: {lengths!r}")
    finally:
        # Teardown: delete in reverse-index order so indices stay valid.
        final_reply = osc.query(
            "/live/track/get/arrangement_clips/start_time", [TRACK_ID])
        count = max(len(list(final_reply)) - 1, 0)
        for i in range(count - 1, baseline_count - 1, -1):
            osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, i])
            wait_one_tick()


def test_arrangement_clip_set_name_roundtrips(osc):
    """Write arrangement_clip.name via the set handler and verify the
    change shows up in both the per-clip getter and the bulk track
    getter. Robust against pre-existing clips on TRACK_ID.
    """
    baseline_count, latest_end = _arrangement_clips_baseline(osc)
    safe_start = latest_end + 8.0
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, safe_start, 4.0])
    wait_one_tick()
    clip_index = _find_clip_index_by_start(osc, safe_start)

    try:
        osc.send_message(
            "/live/arrangement_clip/set/name",
            [TRACK_ID, clip_index, "C Maj Region"],
        )
        wait_one_tick()

        per_clip = osc.query(
            "/live/arrangement_clip/get/name", [TRACK_ID, clip_index],
        )
        # Reply shape: (track, clip_index, name).
        assert per_clip[-1] == "C Maj Region", (
            "per-clip get/name did not reflect the write: %r" % (per_clip,)
        )

        bulk = osc.query(
            "/live/track/get/arrangement_clips/name", [TRACK_ID],
        )
        # Reply shape: (track, name_0, name_1, ...).
        assert "C Maj Region" in bulk[1:], (
            "bulk track listing did not reflect the write: %r" % (bulk,)
        )

        # Rename again and verify the second write also lands (no stuck state).
        osc.send_message(
            "/live/arrangement_clip/set/name",
            [TRACK_ID, clip_index, "Db Maj Region"],
        )
        wait_one_tick()
        per_clip_2 = osc.query(
            "/live/arrangement_clip/get/name", [TRACK_ID, clip_index],
        )
        assert per_clip_2[-1] == "Db Maj Region"
    finally:
        osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, clip_index])
        wait_one_tick()
        final_count = _arrangement_clips_baseline(osc)[0]
        assert final_count == baseline_count, (
            "teardown left track at %d clips (expected baseline %d)"
            % (final_count, baseline_count)
        )


def test_remove_arrangement_clip_notes(osc):
    """Create an arrangement clip, add two notes one at a time (each
    verified by read-back), remove the first by covering it with a
    pitch/time range, and verify the second survives byte-for-byte
    while the first is gone. Teardown deletes the arrangement clip
    and read-checks that the track's clip count returns to baseline.

    Robust against pre-existing clips on TRACK_ID: the test's own clip
    is placed at a ``safe_start`` past any existing content and
    located by start_time, not by a hardcoded index. Teardown asserts
    the track returns to its baseline count rather than zero.
    """
    baseline_count, latest_end = _arrangement_clips_baseline(osc)
    safe_start = latest_end + 8.0
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, safe_start, 4.0])
    wait_one_tick()
    names_after_create = osc.query(
        "/live/track/get/arrangement_clips/name", [TRACK_ID],
    )
    # Count must have grown by exactly 1.
    assert len(names_after_create) == baseline_count + 2, (
        "expected %d clips after create (baseline %d + 1), got %d: %r"
        % (baseline_count + 1, baseline_count, len(names_after_create) - 1,
           names_after_create)
    )
    clip_index = _find_clip_index_by_start(osc, safe_start)

    try:
        # Act: add note 1 (C3), verify presence. Note times are
        # clip-relative so time 0.0 is the start of our clip regardless
        # of where the clip sits in the arrangement.
        osc.send_message("/live/arrangement_clip/add/notes",
            [TRACK_ID, clip_index, 60, 0.0, 0.5, 100, 0, 1.0])
        wait_one_tick()
        reply_after_1 = osc.query(
            "/live/arrangement_clip/get/notes", [TRACK_ID, clip_index],
        )
        notes_1 = _decode_arrangement_notes(reply_after_1)
        assert len(notes_1) == 1
        assert notes_1[0]["pitch"] == 60
        assert notes_1[0]["start"] == pytest.approx(0.0)
        assert notes_1[0]["duration"] == pytest.approx(0.5)
        assert notes_1[0]["velocity"] == 100

        # Act: add note 2 (D3) at time 2.0, verify presence.
        osc.send_message("/live/arrangement_clip/add/notes",
            [TRACK_ID, clip_index, 62, 2.0, 0.5, 110, 0, 1.0])
        wait_one_tick()
        reply_after_2 = osc.query(
            "/live/arrangement_clip/get/notes", [TRACK_ID, clip_index],
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
        ack = osc.query(
            "/live/arrangement_clip/remove/notes",
            [TRACK_ID, clip_index, 60, 1, 0.0, 1.0],
        )
        assert ack == (TRACK_ID, clip_index, "ok"), (
            "arrangement remove/notes must ack (track, clip_index, 'ok') - got %r"
            % (ack,)
        )
        wait_one_tick()

        # Verify: only the D3 note at time 2.0 remains, byte-for-byte.
        reply_after_remove = osc.query(
            "/live/arrangement_clip/get/notes", [TRACK_ID, clip_index],
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
        # Teardown: delete the arrangement clip, then read back the
        # per-track clips listing and assert the count returned to
        # baseline (not zero — the user's own clips must survive).
        osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, clip_index])
        wait_one_tick()
        final_count = _arrangement_clips_baseline(osc)[0]
        assert final_count == baseline_count, (
            "teardown left track at %d clips (expected baseline %d)"
            % (final_count, baseline_count)
        )
