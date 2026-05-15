"""Unit tests for Bridge-side Arrangement View snapshot/delta caching."""

import json


class _Note:
    def __init__(
        self,
        pitch,
        start_time,
        duration,
        velocity,
        mute=False,
        probability=1.0,
    ):
        self.pitch = pitch
        self.start_time = start_time
        self.duration = duration
        self.velocity = velocity
        self.mute = mute
        self.probability = probability


class _Clip:
    def __init__(
        self,
        ptr,
        name,
        start_time,
        length,
        *,
        color=0x445566,
        color_index=12,
        end_time=None,
        looping=False,
        loop_start=None,
        loop_end=None,
        notes=(),
        notes_error=None,
        notes_error_after_calls=None,
    ):
        if ptr is not None:
            self._live_ptr = ptr
        self.name = name
        self.start_time = start_time
        self.length = length
        self.end_time = start_time + length if end_time is None else end_time
        self.looping = looping
        self.loop_start = 0.0 if loop_start is None else loop_start
        self.loop_end = length if loop_end is None else loop_end
        self.color = color
        self.color_index = color_index
        self._notes = list(notes)
        self._notes_error = notes_error
        self._notes_error_after_calls = notes_error_after_calls
        self._notes_call_count = 0

    def get_notes_extended(self, pitch_start, pitch_span, time_start, time_span):
        self._notes_call_count += 1
        if self._notes_error is not None:
            raise self._notes_error
        if (
            self._notes_error_after_calls is not None
            and self._notes_call_count > self._notes_error_after_calls
        ):
            raise RuntimeError("notes should not be read again")
        pitch_end = pitch_start + pitch_span
        time_end = time_start + time_span
        return [
            note for note in self._notes
            if pitch_start <= note.pitch < pitch_end
            and note.start_time < time_end
            and note.start_time + note.duration > time_start
        ]


class _Track:
    def __init__(
        self,
        name,
        clips=(),
        *,
        color=0x112233,
        has_midi_input=True,
        is_foldable=False,
        is_grouped=False,
        group_track=None,
        live_ptr=None,
    ):
        self.name = name
        if live_ptr is not None:
            self._live_ptr = live_ptr
        self.color = color
        self.has_midi_input = has_midi_input
        self.is_foldable = is_foldable
        self.is_grouped = is_grouped
        self.group_track = group_track
        self.arrangement_clips = list(clips)


class _EqualTrack(_Track):
    def __init__(self, name, equality_key, **kwargs):
        super().__init__(name, **kwargs)
        self.equality_key = equality_key

    def __eq__(self, other):
        return (
            isinstance(other, _EqualTrack)
            and self.equality_key == other.equality_key
        )


class _CuePoint:
    def __init__(self, name, time):
        self.name = name
        self.time = time


class _Song:
    def __init__(self, tracks, cue_points=(), current_song_time=0.0):
        self.tracks = list(tracks)
        self.cue_points = list(cue_points)
        self.current_song_time = current_song_time


def _decode(payload):
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def test_build_arrangement_snapshot_includes_live_ptr_clip_ids():
    from abletonosc.arrangement_view import build_arrangement_snapshot

    song = _Song(
        [
            _Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)]),
            _Track("Lead", []),
        ],
        [_CuePoint("Verse", 16.0)],
        current_song_time=12.5,
    )

    data = _decode(build_arrangement_snapshot(song, revision=7))

    assert data["status"] == "ok"
    assert data["schema_version"] == 1
    assert data["revision"] == 7
    assert data["track_names"] == ["Bass", "Lead"]
    assert data["track_indices"] == [0, 1]
    assert data["midi_tracks"] == [True, True]
    assert data["track_colors"] == ["#112233", "#112233"]
    assert data["is_group_tracks"] == [False, False]
    assert data["group_parent_indices"] == [None, None]
    assert data["clips"] == {
        "0": [
            {
                "index": 0,
                "clip_id": "101",
                "name": "Intro",
                "start": 0.0,
                "length": 8.0,
                "end": 8.0,
                "looping": False,
                "loop_start": 0.0,
                "loop_end": 8.0,
                "color": "#445566",
                "color_index": 12,
                "notes_signature": "0:0",
            }
        ],
        "1": [],
    }
    assert data["locators"] == [{"name": "Verse", "time": 16.0}]
    assert data["current_song_time"] == 12.5


def test_build_arrangement_snapshot_includes_loop_metadata():
    from abletonosc.arrangement_view import build_arrangement_snapshot

    song = _Song([
        _Track(
            "Lead",
            [
                _Clip(
                    101,
                    "Looped Hook",
                    8.0,
                    4.0,
                    end_time=24.0,
                    looping=True,
                    loop_start=0.0,
                    loop_end=4.0,
                )
            ],
        )
    ])

    data = _decode(build_arrangement_snapshot(song, revision=1))

    clip = data["clips"]["0"][0]
    assert clip["start"] == 8.0
    assert clip["length"] == 4.0
    assert clip["end"] == 24.0
    assert clip["looping"] is True
    assert clip["loop_start"] == 0.0
    assert clip["loop_end"] == 4.0


def test_build_arrangement_snapshot_includes_group_parent_indices():
    from abletonosc.arrangement_view import build_arrangement_snapshot

    group = _Track("Strings Group", has_midi_input=False, is_foldable=True)
    child = _Track(
        "Violins",
        [_Clip(101, "Verse", 0.0, 8.0)],
        is_grouped=True,
        group_track=group,
    )
    outside = _Track("Bass", [_Clip(201, "Bass", 0.0, 8.0)])
    song = _Song([group, child, outside])

    data = _decode(build_arrangement_snapshot(song, revision=3))

    assert data["status"] == "ok"
    assert data["is_group_tracks"] == [True, False, False]
    assert data["group_parent_indices"] == [None, 0, None]


def test_track_index_for_parent_matches_live_ptr_when_object_identity_differs():
    from abletonosc.arrangement_view import _track_index_for_parent

    parent_from_child = _Track(
        "Strings Group",
        has_midi_input=False,
        is_foldable=True,
        live_ptr=9001,
    )
    listed_group = _Track(
        "Strings Group",
        has_midi_input=False,
        is_foldable=True,
        live_ptr=9001,
    )
    child = _Track("Violins", is_grouped=True, group_track=parent_from_child)

    assert _track_index_for_parent(parent_from_child, [listed_group, child]) == 0


def test_track_index_for_parent_matches_live_object_equality():
    from abletonosc.arrangement_view import _track_index_for_parent

    parent_from_child = _EqualTrack(
        "Strings Group",
        "group-a",
        has_midi_input=False,
        is_foldable=True,
    )
    listed_group = _EqualTrack(
        "Strings Group",
        "group-a",
        has_midi_input=False,
        is_foldable=True,
    )
    child = _Track("Violins", is_grouped=True, group_track=parent_from_child)

    assert _track_index_for_parent(parent_from_child, [listed_group, child]) == 0


def test_build_arrangement_snapshot_reports_missing_identity():
    from abletonosc.arrangement_view import build_arrangement_snapshot

    song = _Song([_Track("Bass", [_Clip(None, "Intro", 0.0, 8.0)])])

    data = _decode(build_arrangement_snapshot(song, revision=1))

    assert data["status"] == "error"
    assert data["code"] == "identity_unavailable"
    assert "Arrangement clip identity is unavailable" in data["message"]


def test_build_arrangement_snapshot_reports_unreadable_clip_notes():
    from abletonosc.arrangement_view import build_arrangement_snapshot

    song = _Song([
        _Track(
            "Bass",
            [
                _Clip(
                    101,
                    "Intro",
                    0.0,
                    8.0,
                    notes_error=RuntimeError("notes temporarily unavailable"),
                )
            ],
        )
    ])

    data = _decode(build_arrangement_snapshot(song, revision=1))

    assert data["status"] == "error"
    assert data["code"] == "notes_unavailable"
    assert "Arrangement clip notes are unavailable" in data["message"]


def test_build_arrangement_snapshot_skips_note_signature_for_audio_tracks():
    from abletonosc.arrangement_view import build_arrangement_snapshot

    audio_clip = _Clip(
        101,
        "Audio Intro",
        0.0,
        8.0,
        notes_error=RuntimeError("audio clips do not expose MIDI notes"),
    )
    song = _Song([
        _Track("Audio", [audio_clip], has_midi_input=False)
    ])

    data = _decode(build_arrangement_snapshot(song, revision=1))

    assert data["status"] == "ok"
    assert data["midi_tracks"] == [False]
    assert data["clips"]["0"][0]["notes_signature"] is None
    assert audio_clip._notes_call_count == 0


def test_delta_cache_returns_no_changes_when_snapshot_is_unchanged():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    song = _Song([_Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)])])
    cache = ArrangementDeltaCache()
    snapshot = cache.snapshot(song)

    delta = _decode(cache.delta(song, since_revision=snapshot["revision"]))

    assert delta == {
        "status": "ok",
        "schema_version": 1,
        "base_revision": snapshot["revision"],
        "revision": snapshot["revision"],
        "changes": [],
    }


def test_delta_cache_replaces_only_changed_track_clips():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    bass = _Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)])
    lead = _Track("Lead", [_Clip(201, "Hook", 16.0, 4.0)])
    song = _Song([bass, lead])
    cache = ArrangementDeltaCache()
    snapshot = cache.snapshot(song)

    lead.arrangement_clips = [_Clip(
        202,
        "Hook 2",
        20.0,
        4.0,
        color=0x778899,
        color_index=18,
    )]
    delta = _decode(cache.delta(song, since_revision=snapshot["revision"]))

    assert delta["status"] == "ok"
    assert delta["base_revision"] == snapshot["revision"]
    assert delta["revision"] == snapshot["revision"] + 1
    assert delta["changes"] == [
        {
            "type": "replace_track_clips",
            "track_index": 1,
            "clips": [
                {
                    "index": 0,
                    "clip_id": "202",
                        "name": "Hook 2",
                        "start": 20.0,
                        "length": 4.0,
                        "end": 24.0,
                        "looping": False,
                        "loop_start": 0.0,
                        "loop_end": 4.0,
                        "color": "#778899",
                        "color_index": 18,
                        "notes_signature": "0:0",
                }
            ],
        }
    ]


def test_delta_cache_replaces_track_clips_when_clip_color_changes():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    bass = _Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)])
    song = _Song([bass])
    cache = ArrangementDeltaCache()
    snapshot = cache.snapshot(song)

    bass.arrangement_clips[0].color = 0x99AABB
    bass.arrangement_clips[0].color_index = 21
    delta = _decode(cache.delta(song, since_revision=snapshot["revision"]))

    assert delta["status"] == "ok"
    assert delta["revision"] == snapshot["revision"] + 1
    assert delta["changes"] == [
        {
            "type": "replace_track_clips",
            "track_index": 0,
            "clips": [
                {
                    "index": 0,
                    "clip_id": "101",
                        "name": "Intro",
                        "start": 0.0,
                        "length": 8.0,
                        "end": 8.0,
                        "looping": False,
                        "loop_start": 0.0,
                        "loop_end": 8.0,
                        "color": "#99aabb",
                        "color_index": 21,
                        "notes_signature": "0:0",
                }
            ],
        }
    ]


def test_delta_cache_replaces_track_clips_when_clip_notes_change():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    bass = _Track(
        "Bass",
        [
            _Clip(
                101,
                "Intro",
                0.0,
                8.0,
                notes=[_Note(48, 0.0, 1.0, 100)],
            )
        ],
    )
    song = _Song([bass])
    cache = ArrangementDeltaCache()
    snapshot = cache.snapshot(song)

    old_signature = snapshot["clips"]["0"][0]["notes_signature"]
    bass.arrangement_clips[0]._notes.append(_Note(50, 1.0, 0.5, 92))

    delta = _decode(cache.delta(song, since_revision=snapshot["revision"]))

    assert delta["status"] == "ok"
    assert delta["revision"] == snapshot["revision"] + 1
    changed = delta["changes"][0]
    assert changed["type"] == "replace_track_clips"
    assert changed["track_index"] == 0
    assert changed["clips"][0]["clip_id"] == "101"
    assert changed["clips"][0]["notes_signature"] != old_signature


def test_delta_cache_returns_no_changes_when_notes_are_unchanged():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    bass = _Track(
        "Bass",
        [
            _Clip(
                101,
                "Intro",
                0.0,
                8.0,
                notes=[_Note(48, 0.0, 1.0, 100), _Note(52, 1.0, 1.0, 95)],
            )
        ],
    )
    song = _Song([bass])
    cache = ArrangementDeltaCache()
    snapshot = cache.snapshot(song)

    delta = _decode(cache.delta(song, since_revision=snapshot["revision"]))

    assert delta["status"] == "ok"
    assert delta["revision"] == snapshot["revision"]
    assert delta["changes"] == []


def test_delta_cache_reports_unreadable_clip_notes():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    bass = _Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)])
    song = _Song([bass])
    cache = ArrangementDeltaCache()
    snapshot = cache.snapshot(song)

    bass.arrangement_clips[0]._notes_error = RuntimeError(
        "notes temporarily unavailable"
    )
    delta = _decode(cache.delta(song, since_revision=snapshot["revision"]))

    assert delta["status"] == "error"
    assert delta["code"] == "notes_unavailable"
    assert "Arrangement clip notes are unavailable" in delta["message"]


def test_delta_cache_reuses_current_body_when_metadata_changes():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    clip = _Clip(101, "Intro", 0.0, 8.0, notes_error_after_calls=2)
    bass = _Track("Bass", [clip])
    song = _Song([bass])
    cache = ArrangementDeltaCache()
    snapshot = cache.snapshot(song)

    bass.name = "Bass Renamed"
    delta = _decode(cache.delta(song, since_revision=snapshot["revision"]))

    assert delta["status"] == "ok"
    assert delta["revision"] == snapshot["revision"] + 1
    assert delta["changes"][0]["type"] == "replace_snapshot"
    replacement = delta["changes"][0]["snapshot"]
    assert replacement["status"] == "ok"
    assert replacement["revision"] == delta["revision"]
    assert replacement["track_names"] == ["Bass Renamed"]
    assert replacement["clips"]["0"][0]["notes_signature"] == "0:0"
    assert clip._notes_call_count == 2


def test_delta_cache_requests_snapshot_when_base_revision_is_unknown():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    song = _Song([_Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)])])
    cache = ArrangementDeltaCache()
    cache.snapshot(song)

    delta = _decode(cache.delta(song, since_revision=999))

    assert delta["status"] == "resync_required"
    assert delta["code"] == "unknown_revision"
