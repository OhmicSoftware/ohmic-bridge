"""Unit tests for Bridge-side Arrangement View snapshot/delta caching."""

import json


class _Clip:
    def __init__(self, ptr, name, start_time, length):
        if ptr is not None:
            self._live_ptr = ptr
        self.name = name
        self.start_time = start_time
        self.length = length


class _Track:
    def __init__(
        self,
        name,
        clips=(),
        *,
        color=0x112233,
        has_midi_input=True,
    ):
        self.name = name
        self.color = color
        self.has_midi_input = has_midi_input
        self.arrangement_clips = list(clips)


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
    assert data["clips"] == {
        "0": [
            {
                "index": 0,
                "clip_id": "101",
                "name": "Intro",
                "start": 0.0,
                "length": 8.0,
            }
        ],
        "1": [],
    }
    assert data["locators"] == [{"name": "Verse", "time": 16.0}]
    assert data["current_song_time"] == 12.5


def test_build_arrangement_snapshot_reports_missing_identity():
    from abletonosc.arrangement_view import build_arrangement_snapshot

    song = _Song([_Track("Bass", [_Clip(None, "Intro", 0.0, 8.0)])])

    data = _decode(build_arrangement_snapshot(song, revision=1))

    assert data["status"] == "error"
    assert data["code"] == "identity_unavailable"
    assert "Arrangement clip identity is unavailable" in data["message"]


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

    lead.arrangement_clips = [_Clip(202, "Hook 2", 20.0, 4.0)]
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
                }
            ],
        }
    ]


def test_delta_cache_requests_snapshot_when_base_revision_is_unknown():
    from abletonosc.arrangement_view import ArrangementDeltaCache

    song = _Song([_Track("Bass", [_Clip(101, "Intro", 0.0, 8.0)])])
    cache = ArrangementDeltaCache()
    cache.snapshot(song)

    delta = _decode(cache.delta(song, since_revision=999))

    assert delta["status"] == "resync_required"
    assert delta["code"] == "unknown_revision"
