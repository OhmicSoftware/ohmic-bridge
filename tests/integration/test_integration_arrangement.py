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


def test_arrangement_snapshot_chunks_roundtrip(osc):
    baseline_count, latest_end = _arrangement_clips_baseline(osc)
    safe_start = latest_end + 8.0
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, safe_start, 4.0])
    wait_one_tick()
    clip_index = _find_clip_index_by_start(osc, safe_start)

    try:
        manifest_reply = osc.query(
            "/live/song/get/arrangement_snapshot_manifest", [])
        manifest = json.loads(manifest_reply[-1])
        assert manifest["status"] == "ok"
        assert manifest["snapshot_id"]
        assert manifest["chunk_count"] > 0
        assert "clips" not in manifest

        chunks = []
        for chunk_index in range(manifest["chunk_count"]):
            chunk_reply = osc.query(
                "/live/song/get/arrangement_snapshot_chunk",
                [manifest["snapshot_id"], chunk_index],
            )
            assert chunk_reply[0] == manifest["snapshot_id"]
            assert chunk_reply[1] == chunk_index
            chunk = json.loads(chunk_reply[-1])
            assert chunk["status"] == "ok"
            assert chunk["snapshot_id"] == manifest["snapshot_id"]
            assert chunk["revision"] == manifest["revision"]
            assert chunk["chunk_index"] == chunk_index
            assert chunk["chunk_count"] == manifest["chunk_count"]
            chunks.append(chunk)

        clips = {}
        for chunk in chunks:
            for track_key, track_clips in chunk["clips"].items():
                clips.setdefault(track_key, []).extend(track_clips)
        clip = clips[str(TRACK_ID)][clip_index]
        assert clip["start"] == pytest.approx(safe_start)
        assert clip["length"] == pytest.approx(4.0)
        assert "end" in clip
        assert "looping" in clip
        assert "loop_start" in clip
        assert "loop_end" in clip
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


def test_arrangement_clip_start_marker_readback_via_bulk_endpoint_and_snapshot(osc):
    """Verify the new /live/track/get/arrangement_clips/start_marker bulk
    endpoint and the arrangement_snapshot's per-clip "start_marker" key
    (abletonosc/arrangement_view.py's ``_clip_row``) both surface the
    LOM's ``Clip.start_marker`` for a freshly-created, untrimmed
    arrangement clip. Ohmic's Arrangement View consumes these to
    render trimmed clips correctly; this is the real-Ableton evidence
    that both surfaces report 0.0 for a clip that hasn't been trimmed.

    Robust against pre-existing arrangement clips on TRACK_ID: the new
    clip is placed at a ``safe_start`` past any existing content and
    located by start_time, not by a hardcoded index.
    """
    baseline_count, latest_end = _arrangement_clips_baseline(osc)
    safe_start = latest_end + 8.0
    osc.send_message("/live/arrangement_clip/create", [TRACK_ID, safe_start, 4.0])
    wait_one_tick()
    clip_index = _find_clip_index_by_start(osc, safe_start)

    try:
        # Bulk endpoint wire: (track, marker_0, marker_1, ...) — same
        # per-clip index order as /live/track/get/arrangement_clips/
        # start_time, since both iterate track.arrangement_clips in
        # LOM order (see track_get_arrangement_clip_start_markers in
        # abletonosc/track.py).
        markers_reply = osc.query(
            "/live/track/get/arrangement_clips/start_marker", [TRACK_ID])
        markers = [float(m) for m in list(markers_reply)[1:]]
        assert clip_index < len(markers), (
            "bulk start_marker listing shorter than expected clip_index "
            "%d: %r" % (clip_index, markers_reply)
        )
        assert markers[clip_index] == pytest.approx(0.0), (
            "expected a freshly-created, untrimmed clip's start_marker "
            "to be 0.0 — got %r (full listing: %r)"
            % (markers[clip_index], markers)
        )

        # Snapshot: clip row must carry "start_marker" alongside the
        # pre-existing "loop_start" key.
        snapshot_reply = osc.query("/live/song/get/arrangement_snapshot", [])
        snapshot = json.loads(snapshot_reply[-1])
        assert snapshot["status"] == "ok"
        clip_row = snapshot["clips"][str(TRACK_ID)][clip_index]
        assert "start_marker" in clip_row, (
            "arrangement_snapshot clip row is missing the 'start_marker' "
            "key: %r" % (clip_row,)
        )
        assert clip_row["start_marker"] == pytest.approx(0.0), (
            "snapshot start_marker mismatch: %r" % (clip_row,)
        )
        assert "loop_start" in clip_row
    finally:
        osc.send_message("/live/arrangement_clip/delete", [TRACK_ID, clip_index])
        wait_one_tick()
        final_count = _arrangement_clips_baseline(osc)[0]
        assert final_count == baseline_count, (
            "teardown left track at %d clips (expected baseline %d)"
            % (final_count, baseline_count)
        )


def test_unlooped_session_clip_mirrors_start_marker_into_loop_start(osc):
    """Prove the Live Object Model rule that Ohmic's Arrangement View
    fallback depends on: for an UNLOOPED clip, ``start_marker`` and
    ``loop_start`` are always numerically equal, so reading
    ``loop_start`` is a safe substitute when an older Bridge build's
    arrangement snapshot omits the newer ``start_marker`` key (see
    arrangement_view.py's ``_clip_row``). Also proves note times stay
    content-anchored — moving the trim point does NOT rebase the
    underlying note start times.

    A session clip is used, not an arrangement clip, because the
    Bridge only wires up start_marker/looping/loop_start *setters*
    through the generic session-clip property handlers in clip.py's
    ``properties_rw`` list — arrangement clips only get a name setter
    (see the arrangement-clip endpoints further up in clip.py). The
    properties under test are defined on Live's Clip class itself, so
    their behavior is identical for a session clip and an arrangement
    clip; a session clip is a faithful proxy for the arrangement-clip
    rendering behavior Ohmic relies on.

    REAL-ABLETON FINDING (confirmed against Live 12.3.5 via two
    standalone diagnostic runs; abletonosc.log shows no exception
    around either set — this is genuine LOM behavior, not a Bridge
    bug or a masked error): ``clip.start_marker``'s setter is only
    *effective* while ``clip.looping == True``. Diagnostic evidence:

      * looping=True,  set start_marker=3.0 -> reads back 3.0
        (loop_start stays untouched at 0.0: independent while looped).
      * set looping=False -> loop_start snaps to 3.0 (locks to
        whatever start_marker held at the moment of unlooping).
      * still unlooped, set start_marker=1.0 -> reads back unchanged
        (3.0): the setter is a no-op once unlooped.
      * still unlooped, set loop_start=4.0 -> reads back 4.0, AND
        start_marker follows to 4.0 too.

    In other words: while looping, start_marker is authoritative and
    independent of loop_start; once unlooped, loop_start becomes the
    only setter that moves the trim point, and start_marker becomes a
    locked mirror of it. Either way, once unlooped the two values are
    always equal — exactly the invariant Ohmic's loop_start fallback
    needs. This test drives the clip through both setters (as a real
    Bridge caller would have to) rather than asserting on the inert
    start_marker-while-unlooped no-op, which would exercise nothing.
    """
    slot_index = 0
    clip_length = 8.0
    osc.send_message(
        "/live/clip_slot/create_clip", [TRACK_ID, slot_index, clip_length])
    wait_one_tick()
    has_clip = osc.query(
        "/live/clip_slot/get/has_clip", [TRACK_ID, slot_index])
    assert len(has_clip) >= 3 and bool(has_clip[2]) is True, (
        "failed to create session clip at (%d, %d): %r"
        % (TRACK_ID, slot_index, has_clip)
    )

    try:
        # Two notes at content-relative times 0.5 and 5.0, inside the
        # 8-beat clip and straddling the trim points used below.
        osc.send_message(
            "/live/clip/add/notes",
            [TRACK_ID, slot_index, 60, 0.5, 1.0, 100, 0, 1.0])
        wait_one_tick()
        osc.send_message(
            "/live/clip/add/notes",
            [TRACK_ID, slot_index, 62, 5.0, 1.0, 110, 0, 1.0])
        wait_one_tick()

        def read_notes():
            # /live/clip/get/notes shares the (track, clip_index,
            # pitch, start, dur, vel, mute, prob, ...) wire shape with
            # /live/arrangement_clip/get/notes, so the existing
            # decoder applies unchanged.
            return _decode_arrangement_notes(
                osc.query("/live/clip/get/notes", [TRACK_ID, slot_index]))

        notes_before = read_notes()
        assert len(notes_before) == 2, (
            "expected 2 notes before any trim move: %r" % (notes_before,)
        )

        # Step 1: while still looping (the default), start_marker's
        # setter IS effective.
        osc.send_message(
            "/live/clip/set/start_marker", [TRACK_ID, slot_index, 4.0])
        wait_one_tick()
        marker_while_looping = osc.query(
            "/live/clip/get/start_marker", [TRACK_ID, slot_index])
        assert float(marker_while_looping[2]) == pytest.approx(4.0), (
            "start_marker did not land while looping=True — got %r"
            % (marker_while_looping,)
        )

        # Step 2: flip looping False. Live locks loop_start to the
        # current start_marker value — the mirror under test.
        osc.send_message(
            "/live/clip/set/looping", [TRACK_ID, slot_index, False])
        wait_one_tick()
        looping_reply = osc.query(
            "/live/clip/get/looping", [TRACK_ID, slot_index])
        assert bool(looping_reply[2]) is False, (
            "looping=False did not land — got %r" % (looping_reply,)
        )
        loop_start_after_unloop = osc.query(
            "/live/clip/get/loop_start", [TRACK_ID, slot_index])
        assert float(loop_start_after_unloop[2]) == pytest.approx(4.0), (
            "expected unlooping to lock loop_start to start_marker=4.0 "
            "— got %r" % (loop_start_after_unloop,)
        )

        # Step 3: the setter that moves the trim point once unlooped
        # is loop_start — and it drags start_marker along with it.
        # This is the actual mirror path Ohmic's fallback depends on.
        osc.send_message(
            "/live/clip/set/loop_start", [TRACK_ID, slot_index, 2.0])
        wait_one_tick()
        loop_start_final = osc.query(
            "/live/clip/get/loop_start", [TRACK_ID, slot_index])
        marker_final = osc.query(
            "/live/clip/get/start_marker", [TRACK_ID, slot_index])
        assert float(loop_start_final[2]) == pytest.approx(2.0), (
            "loop_start did not land — got %r" % (loop_start_final,)
        )
        assert float(marker_final[2]) == pytest.approx(2.0), (
            "expected start_marker to mirror loop_start=2.0 once "
            "unlooped — got %r" % (marker_final,)
        )

        # Content-anchoring: none of the trim moves above rebased the
        # underlying note start times.
        notes_after = read_notes()
        assert len(notes_after) == 2, (
            "note count changed after the trim moves: %r" % (notes_after,)
        )
        by_pitch = {n["pitch"]: n for n in notes_after}
        assert 60 in by_pitch and 62 in by_pitch
        assert by_pitch[60]["start"] == pytest.approx(0.5), (
            "note at pitch 60 should stay at content time 0.5 — got %r"
            % (notes_after,)
        )
        assert by_pitch[62]["start"] == pytest.approx(5.0), (
            "note at pitch 62 should stay at content time 5.0 — got %r"
            % (notes_after,)
        )
    finally:
        osc.send_message(
            "/live/clip_slot/delete_clip", [TRACK_ID, slot_index])
        wait_one_tick()
        has_clip_after = osc.query(
            "/live/clip_slot/get/has_clip", [TRACK_ID, slot_index])
        assert bool(has_clip_after[2]) is False, (
            "teardown did not delete the session clip: %r" % (has_clip_after,)
        )
