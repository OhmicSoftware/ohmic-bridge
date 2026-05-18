"""Unit tests for Bridge-side Arrangement View snapshot/delta caching."""

import json
import sys
import types


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


class _Component:
    @property
    def song(self):
        return self.manager.song


class _OscServer:
    def __init__(self):
        self.handlers = {}

    def add_handler(self, address, callback):
        self.handlers[address] = callback


class _Manager:
    def __init__(self, song):
        self.song = song
        self.osc_server = _OscServer()


def _install_song_handler_stubs():
    originals = {
        name: sys.modules.get(name)
        for name in [
            "Live",
            "Live.Track",
            "ableton",
            "ableton.v2",
            "ableton.v2.control_surface",
            "ableton.v2.control_surface.component",
            "abletonosc.handler",
            "abletonosc.osc_server",
            "abletonosc.song",
        ]
    }

    live = types.ModuleType("Live")
    live_track = types.ModuleType("Live.Track")
    live_track.Track = type("Track", (), {})
    live.Track = live_track

    ableton = types.ModuleType("ableton")
    ableton_v2 = types.ModuleType("ableton.v2")
    control_surface = types.ModuleType("ableton.v2.control_surface")
    component = types.ModuleType("ableton.v2.control_surface.component")
    component.Component = _Component

    osc_server = types.ModuleType("abletonosc.osc_server")
    osc_server.OSCServer = _OscServer

    sys.modules["Live"] = live
    sys.modules["Live.Track"] = live_track
    sys.modules["ableton"] = ableton
    sys.modules["ableton.v2"] = ableton_v2
    sys.modules["ableton.v2.control_surface"] = control_surface
    sys.modules["ableton.v2.control_surface.component"] = component
    sys.modules["abletonosc.osc_server"] = osc_server
    sys.modules.pop("abletonosc.handler", None)
    sys.modules.pop("abletonosc.song", None)
    return originals


def _restore_song_handler_stubs(originals):
    for name, module in originals.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _decode(payload):
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def _encoded_size(payload):
    return len(json.dumps(payload).encode("utf-8"))


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


def test_build_arrangement_snapshot_chunks_keep_many_clips_under_budget():
    from abletonosc.arrangement_view import (
        MAX_ARRANGEMENT_SNAPSHOT_CHUNK_BYTES,
        build_arrangement_snapshot,
        build_arrangement_snapshot_chunks,
    )

    tracks = []
    clip_ptr = 1000
    for track_index in range(18):
        clips = []
        for clip_index in range(9):
            clips.append(_Clip(
                clip_ptr,
                f"Section {track_index:02d} Clip {clip_index:02d} "
                + ("Long arrangement clip name " * 8),
                float(clip_index * 4),
                4.0,
            ))
            clip_ptr += 1
        tracks.append(_Track(f"Track {track_index:02d}", clips))
    song = _Song(tracks, [_CuePoint("Drop", 64.0)], current_song_time=12.5)

    chunked = build_arrangement_snapshot_chunks(song, revision=42)
    full = build_arrangement_snapshot(song, revision=42)

    manifest = chunked["manifest"]
    chunks = chunked["chunks"]
    assert manifest["status"] == "ok"
    assert manifest["schema_version"] == 1
    assert manifest["revision"] == 42
    assert manifest["chunk_count"] == len(chunks)
    assert manifest["track_names"] == full["track_names"]
    assert manifest["track_indices"] == full["track_indices"]
    assert manifest["midi_tracks"] == full["midi_tracks"]
    assert manifest["track_colors"] == full["track_colors"]
    assert manifest["is_group_tracks"] == full["is_group_tracks"]
    assert manifest["group_parent_indices"] == full["group_parent_indices"]
    assert manifest["locators"] == full["locators"]
    assert manifest["locators_available"] == full["locators_available"]
    assert manifest["current_song_time"] == full["current_song_time"]
    assert "clips" not in manifest
    assert chunks
    assert all(
        _encoded_size(chunk) <= MAX_ARRANGEMENT_SNAPSHOT_CHUNK_BYTES
        for chunk in chunks
    )
    assert all(chunk["status"] == "ok" for chunk in chunks)
    assert all(chunk["schema_version"] == 1 for chunk in chunks)
    assert all(chunk["revision"] == 42 for chunk in chunks)
    assert all(chunk["chunk_count"] == len(chunks) for chunk in chunks)
    assert all(chunk["snapshot_id"] == manifest["snapshot_id"] for chunk in chunks)
    assert [chunk["chunk_index"] for chunk in chunks] == list(range(len(chunks)))
    rebuilt_clips = {}
    for chunk in chunks:
        rebuilt_clips.update(chunk["clips"])
    assert rebuilt_clips == full["clips"]


def test_build_arrangement_snapshot_chunks_page_single_oversized_track():
    from abletonosc.arrangement_view import build_arrangement_snapshot_chunks

    max_payload_bytes = 2400
    clips = [
        _Clip(
            2000 + index,
            f"One large track clip {index:02d} " + ("payload " * 20),
            float(index * 2),
            2.0,
        )
        for index in range(18)
    ]
    song = _Song([_Track("Dense Track", clips)])

    chunked = build_arrangement_snapshot_chunks(
        song,
        revision=43,
        max_payload_bytes=max_payload_bytes,
    )

    chunks = chunked["chunks"]
    assert chunked["manifest"]["chunk_count"] == len(chunks)
    assert len(chunks) > 1
    assert all(set(chunk["clips"]) == {"0"} for chunk in chunks)
    assert all(_encoded_size(chunk) <= max_payload_bytes for chunk in chunks)
    rebuilt_clips = []
    for chunk in chunks:
        rebuilt_clips.extend(chunk["clips"]["0"])
    assert [clip["index"] for clip in rebuilt_clips] == list(range(len(clips)))


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


def test_build_arrangement_snapshot_chunks_reports_oversized_clip_payload():
    from abletonosc.arrangement_view import build_arrangement_snapshot_chunks

    song = _Song([
        _Track("Bass", [_Clip(101, "Intro " + ("x" * 500), 0.0, 8.0)])
    ])

    data = build_arrangement_snapshot_chunks(
        song,
        revision=1,
        max_payload_bytes=250,
    )

    assert data["status"] == "error"
    assert data["schema_version"] == 1
    assert data["code"] == "chunk_too_large"
    assert "chunk byte budget" in data["message"]


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


def test_delta_cache_snapshot_manifest_caches_matching_chunks():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    song = _Song([
        _Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)]),
        _Track("Lead", [_Clip(201, "Hook", 16.0, 4.0)]),
    ])
    cache = ArrangementDeltaCache()

    manifest = cache.snapshot_manifest(song)

    assert manifest["status"] == "ok"
    assert manifest["revision"] == 1
    assert manifest["snapshot_id"]
    assert manifest["chunk_count"] > 0
    chunks = [
        cache.snapshot_chunk(manifest["snapshot_id"], index)
        for index in range(manifest["chunk_count"])
    ]
    assert all(chunk["status"] == "ok" for chunk in chunks)
    assert all(
        chunk["snapshot_id"] == manifest["snapshot_id"]
        for chunk in chunks
    )
    assert [chunk["chunk_index"] for chunk in chunks] == list(
        range(manifest["chunk_count"])
    )
    rebuilt_clips = {}
    for chunk in chunks:
        rebuilt_clips.update(chunk["clips"])
    assert rebuilt_clips == {
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
        "1": [
            {
                "index": 0,
                "clip_id": "201",
                "name": "Hook",
                "start": 16.0,
                "length": 4.0,
                "end": 20.0,
                "looping": False,
                "loop_start": 0.0,
                "loop_end": 4.0,
                "color": "#445566",
                "color_index": 12,
                "notes_signature": "0:0",
            }
        ],
    }


def test_delta_cache_snapshot_chunk_returns_json_safe_errors():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    song = _Song([_Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)])])
    cache = ArrangementDeltaCache()
    manifest = cache.snapshot_manifest(song)

    wrong_id = cache.snapshot_chunk("missing-snapshot", 0)
    invalid_index = cache.snapshot_chunk(manifest["snapshot_id"], 999)
    non_integer_index = cache.snapshot_chunk(manifest["snapshot_id"], "not-int")

    assert wrong_id["status"] == "error"
    assert wrong_id["code"] == "unknown_snapshot"
    assert invalid_index["status"] == "error"
    assert invalid_index["code"] == "invalid_chunk_index"
    assert non_integer_index["status"] == "error"
    assert non_integer_index["code"] == "invalid_chunk_index"
    json.dumps(wrong_id)
    json.dumps(invalid_index)
    json.dumps(non_integer_index)


def test_delta_cache_snapshot_manifest_keeps_only_active_chunk_set():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    cache = ArrangementDeltaCache()
    first = cache.snapshot_manifest(_Song([
        _Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)])
    ]))
    second = cache.snapshot_manifest(_Song([
        _Track("Lead", [_Clip(201, "Hook", 16.0, 4.0)])
    ]))

    assert first["snapshot_id"] != second["snapshot_id"]
    assert cache.snapshot_chunk(first["snapshot_id"], 0)["status"] == "error"
    assert cache.snapshot_chunk(second["snapshot_id"], 0)["status"] == "ok"


def test_delta_cache_snapshot_manifest_keeps_paged_track_baseline_unchanged():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    clips = [
        _Clip(
            3000 + index,
            f"Paged track clip {index:02d} " + ("payload " * 80),
            float(index * 2),
            2.0,
        )
        for index in range(30)
    ]
    song = _Song([_Track("Dense Track", clips)])
    cache = ArrangementDeltaCache()

    manifest = cache.snapshot_manifest(song)
    delta = cache.delta(song, since_revision=manifest["revision"])

    assert manifest["chunk_count"] > 1
    assert delta == {
        "status": "ok",
        "schema_version": 1,
        "base_revision": manifest["revision"],
        "revision": manifest["revision"],
        "changes": [],
    }


def test_song_handler_exposes_arrangement_snapshot_chunk_endpoints():
    originals = _install_song_handler_stubs()
    try:
        from abletonosc.song import SongHandler

        song = _Song([
            _Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)]),
            _Track("Lead", [_Clip(201, "Hook", 16.0, 4.0)]),
        ])
        manager = _Manager(song)
        SongHandler(manager)

        manifest_reply = manager.osc_server.handlers[
            "/live/song/get/arrangement_snapshot_manifest"
        ](())
        manifest = json.loads(manifest_reply[0])

        assert manifest["status"] == "ok"
        assert manifest["snapshot_id"]
        assert manifest["chunk_count"] > 0
        assert "clips" not in manifest

        chunk_reply = manager.osc_server.handlers[
            "/live/song/get/arrangement_snapshot_chunk"
        ]((manifest["snapshot_id"], 0))
        chunk = json.loads(chunk_reply[2])

        assert chunk_reply[:2] == (manifest["snapshot_id"], 0)
        assert chunk["status"] == "ok"
        assert chunk["snapshot_id"] == manifest["snapshot_id"]
        assert chunk["chunk_index"] == 0
        assert chunk["chunk_count"] == manifest["chunk_count"]
        assert chunk["clips"]["0"][0]["name"] == "Intro"
        assert chunk["clips"]["1"][0]["name"] == "Hook"
    finally:
        _restore_song_handler_stubs(originals)


def test_song_handler_snapshot_chunk_endpoint_returns_json_safe_errors():
    originals = _install_song_handler_stubs()
    try:
        from abletonosc.song import SongHandler

        song = _Song([_Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)])])
        manager = _Manager(song)
        SongHandler(manager)

        manifest_reply = manager.osc_server.handlers[
            "/live/song/get/arrangement_snapshot_manifest"
        ](())
        manifest = json.loads(manifest_reply[0])
        chunk_handler = manager.osc_server.handlers[
            "/live/song/get/arrangement_snapshot_chunk"
        ]

        wrong_id_reply = chunk_handler(("missing-snapshot", 0))
        invalid_index_reply = chunk_handler((manifest["snapshot_id"], 999))
        non_integer_index_reply = chunk_handler((
            manifest["snapshot_id"],
            "not-int",
        ))

        wrong_id = json.loads(wrong_id_reply[2])
        invalid_index = json.loads(invalid_index_reply[2])
        non_integer_index = json.loads(non_integer_index_reply[2])

        assert wrong_id_reply[:2] == ("missing-snapshot", 0)
        assert wrong_id["status"] == "error"
        assert wrong_id["code"] == "unknown_snapshot"
        assert invalid_index_reply[:2] == (manifest["snapshot_id"], 999)
        assert invalid_index["status"] == "error"
        assert invalid_index["code"] == "invalid_chunk_index"
        assert non_integer_index_reply[:2] == (
            manifest["snapshot_id"],
            "not-int",
        )
        assert non_integer_index["status"] == "error"
        assert non_integer_index["code"] == "invalid_chunk_index"
    finally:
        _restore_song_handler_stubs(originals)


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
