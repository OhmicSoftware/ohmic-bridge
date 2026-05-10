"""Bridge-side Arrangement View snapshot and delta model."""

from __future__ import annotations

import copy

SCHEMA_VERSION = 1


class ArrangementIdentityError(RuntimeError):
    """Raised when a Live arrangement clip lacks usable object identity."""


def _color_hex(value) -> str:
    try:
        color = int(value)
    except Exception:
        color = 0
    return "#%02x%02x%02x" % (
        (color >> 16) & 0xFF,
        (color >> 8) & 0xFF,
        color & 0xFF,
    )


def _optional_color_hex(target) -> str | None:
    try:
        value = getattr(target, "color")
    except Exception:
        return None
    if value is None:
        return None
    return _color_hex(value)


def _optional_int(target, attr: str) -> int | None:
    try:
        value = getattr(target, attr)
    except Exception:
        return None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clip_id(clip) -> str:
    try:
        value = getattr(clip, "_live_ptr")
    except Exception as exc:
        raise ArrangementIdentityError(
            "Arrangement clip identity is unavailable in this Live session."
        ) from exc
    if value is None:
        raise ArrangementIdentityError(
            "Arrangement clip identity is unavailable in this Live session."
        )
    return str(int(value))


def _clip_row(clip, clip_index: int) -> dict:
    return {
        "index": int(clip_index),
        "clip_id": _clip_id(clip),
        "name": str(getattr(clip, "name", "")),
        "start": float(getattr(clip, "start_time", 0.0)),
        "length": float(getattr(clip, "length", 0.0)),
        "color": _optional_color_hex(clip),
        "color_index": _optional_int(clip, "color_index"),
    }


def _track_index_for_parent(parent, tracks: list) -> int | None:
    if parent is None:
        return None
    for index, track in enumerate(tracks):
        if track is parent:
            return index
    for index, track in enumerate(tracks):
        try:
            if track == parent:
                return index
        except Exception:
            continue

    parent_ptr = getattr(parent, "_live_ptr", None)
    if parent_ptr is None:
        return None
    for index, track in enumerate(tracks):
        if getattr(track, "_live_ptr", None) == parent_ptr:
            return index
    return None


def _safe_bool_attr(target, attr: str) -> bool:
    try:
        return bool(getattr(target, attr, False))
    except Exception:
        return False


def _safe_attr(target, attr: str):
    try:
        return getattr(target, attr, None)
    except Exception:
        return None


def _snapshot_body(song) -> dict:
    tracks = list(getattr(song, "tracks", []))
    track_names = []
    track_indices = []
    midi_tracks = []
    track_colors = []
    track_mutes = []
    track_solos = []
    track_arms = []
    is_group_tracks = []
    group_parent_indices = []
    clips: dict[str, list[dict]] = {}

    for track_index, track in enumerate(tracks):
        track_names.append(str(getattr(track, "name", "")))
        track_indices.append(track_index)
        midi_tracks.append(bool(getattr(track, "has_midi_input", False)))
        track_colors.append(_color_hex(getattr(track, "color", 0)))
        track_mutes.append(_safe_bool_attr(track, "mute"))
        track_solos.append(_safe_bool_attr(track, "solo"))
        track_arms.append(_safe_bool_attr(track, "arm"))
        is_group_tracks.append(_safe_bool_attr(track, "is_foldable"))
        parent = _safe_attr(track, "group_track")
        group_parent_indices.append(_track_index_for_parent(parent, tracks))
        try:
            arrangement_clips = list(getattr(track, "arrangement_clips", []))
        except Exception:
            arrangement_clips = []
        clips[str(track_index)] = [
            _clip_row(clip, clip_index)
            for clip_index, clip in enumerate(arrangement_clips)
        ]

    locators = []
    for cue in list(getattr(song, "cue_points", [])):
        locators.append({
            "name": str(getattr(cue, "name", "")),
            "time": float(getattr(cue, "time", 0.0)),
        })

    current_song_time = None
    try:
        current_song_time = float(getattr(song, "current_song_time"))
    except Exception:
        current_song_time = None

    return {
        "track_names": track_names,
        "track_indices": track_indices,
        "midi_tracks": midi_tracks,
        "track_colors": track_colors,
        "track_mutes": track_mutes,
        "track_solos": track_solos,
        "track_arms": track_arms,
        "is_group_tracks": is_group_tracks,
        "group_parent_indices": group_parent_indices,
        "clips": clips,
        "locators": locators,
        "locators_available": True,
        "current_song_time": current_song_time,
    }


def _error_payload(code: str, message: str) -> dict:
    return {
        "status": "error",
        "schema_version": SCHEMA_VERSION,
        "code": code,
        "message": message,
    }


def build_arrangement_snapshot(song, *, revision: int) -> dict:
    """Build a complete Arrangement View snapshot from Live's current song."""
    try:
        body = _snapshot_body(song)
    except ArrangementIdentityError as exc:
        return _error_payload("identity_unavailable", str(exc))
    body.update({
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "revision": int(revision),
    })
    return body


def _metadata_changed(previous: dict, current: dict) -> bool:
    keys = (
        "track_names",
        "track_indices",
        "midi_tracks",
        "track_colors",
        "track_mutes",
        "track_solos",
        "track_arms",
        "is_group_tracks",
        "group_parent_indices",
    )
    return any(previous.get(key) != current.get(key) for key in keys)


class ArrangementDeltaCache:
    """Keep Bridge-owned Arrangement state and produce revisioned deltas."""

    def __init__(self):
        self._revision = 0
        self._snapshot: dict | None = None

    @property
    def revision(self) -> int:
        return self._revision

    def snapshot(self, song) -> dict:
        self._revision += 1
        snapshot = build_arrangement_snapshot(song, revision=self._revision)
        if snapshot.get("status") == "ok":
            self._snapshot = copy.deepcopy(snapshot)
        return snapshot

    def delta(self, song, *, since_revision: int) -> dict:
        if self._snapshot is None or int(since_revision) != self._revision:
            return {
                "status": "resync_required",
                "schema_version": SCHEMA_VERSION,
                "code": "unknown_revision",
                "message": "Arrangement cache revision is not available.",
            }

        try:
            current = _snapshot_body(song)
        except ArrangementIdentityError as exc:
            return _error_payload("identity_unavailable", str(exc))

        previous = self._snapshot
        changes = []

        if _metadata_changed(previous, current):
            self._revision += 1
            full = build_arrangement_snapshot(song, revision=self._revision)
            if full.get("status") == "ok":
                self._snapshot = copy.deepcopy(full)
            return {
                "status": "ok",
                "schema_version": SCHEMA_VERSION,
                "base_revision": int(since_revision),
                "revision": self._revision,
                "changes": [
                    {
                        "type": "replace_snapshot",
                        "snapshot": full,
                    }
                ],
            }

        for track_key, clips in current.get("clips", {}).items():
            if previous.get("clips", {}).get(track_key, []) != clips:
                changes.append({
                    "type": "replace_track_clips",
                    "track_index": int(track_key),
                    "clips": clips,
                })

        if previous.get("locators", []) != current.get("locators", []):
            changes.append({
                "type": "replace_locators",
                "locators": current.get("locators", []),
            })

        if not changes:
            return {
                "status": "ok",
                "schema_version": SCHEMA_VERSION,
                "base_revision": int(since_revision),
                "revision": self._revision,
                "changes": [],
            }

        self._revision += 1
        current.update({
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "revision": self._revision,
        })
        self._snapshot = copy.deepcopy(current)
        return {
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "base_revision": int(since_revision),
            "revision": self._revision,
            "changes": changes,
        }
