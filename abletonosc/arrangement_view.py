"""Bridge-side Arrangement View snapshot and delta model."""

from __future__ import annotations

import copy
import hashlib
import json

SCHEMA_VERSION = 1
MAX_ARRANGEMENT_SNAPSHOT_CHUNK_BYTES = 8192


class ArrangementIdentityError(RuntimeError):
    """Raised when a Live arrangement clip lacks usable object identity."""


class ArrangementNoteSignatureError(RuntimeError):
    """Raised when a Live arrangement clip's notes cannot be read."""


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


def _optional_float(target, attr: str) -> float | None:
    try:
        value = getattr(target, attr)
    except Exception:
        return None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _note_field(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _clip_notes_signature(clip) -> str:
    try:
        notes = list(clip.get_notes_extended(0, 128, -8192, 16384) or [])
    except Exception as exc:
        raise ArrangementNoteSignatureError(
            "Arrangement clip notes are unavailable in this Live session."
        ) from exc

    rows = []
    for note in notes:
        rows.append((
            int(getattr(note, "pitch", 0)),
            _note_field(getattr(note, "start_time", 0.0)),
            _note_field(getattr(note, "duration", 0.0)),
            int(getattr(note, "velocity", 0)),
            1 if bool(getattr(note, "mute", False)) else 0,
            _note_field(getattr(note, "probability", 1.0)),
        ))
    if not rows:
        return "0:0"
    rows.sort()
    digest = hashlib.sha1(repr(rows).encode("utf-8")).hexdigest()[:16]
    return f"{len(rows)}:{digest}"


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


def _clip_row(clip, clip_index: int, *, is_midi_track: bool) -> dict:
    start = float(getattr(clip, "start_time", 0.0))
    length = float(getattr(clip, "length", 0.0))
    end = _optional_float(clip, "end_time")
    return {
        "index": int(clip_index),
        "clip_id": _clip_id(clip),
        "name": str(getattr(clip, "name", "")),
        "start": start,
        "length": length,
        "end": end if end is not None else start + length,
        "looping": bool(getattr(clip, "looping", False)),
        "loop_start": _optional_float(clip, "loop_start"),
        "loop_end": _optional_float(clip, "loop_end"),
        "color": _optional_color_hex(clip),
        "color_index": _optional_int(clip, "color_index"),
        "notes_signature": (
            _clip_notes_signature(clip) if is_midi_track else None
        ),
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
    is_group_tracks = []
    group_parent_indices = []
    clips: dict[str, list[dict]] = {}

    for track_index, track in enumerate(tracks):
        track_names.append(str(getattr(track, "name", "")))
        track_indices.append(track_index)
        is_midi_track = bool(getattr(track, "has_midi_input", False))
        midi_tracks.append(is_midi_track)
        track_colors.append(_color_hex(getattr(track, "color", 0)))
        is_group_tracks.append(_safe_bool_attr(track, "is_foldable"))
        parent = _safe_attr(track, "group_track")
        group_parent_indices.append(_track_index_for_parent(parent, tracks))
        try:
            arrangement_clips = list(getattr(track, "arrangement_clips", []))
        except Exception:
            arrangement_clips = []
        clips[str(track_index)] = [
            _clip_row(clip, clip_index, is_midi_track=is_midi_track)
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
    except ArrangementNoteSignatureError as exc:
        return _error_payload("notes_unavailable", str(exc))
    body.update({
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "revision": int(revision),
    })
    return body


def _encoded_json_size(payload: dict) -> int:
    return len(json.dumps(payload).encode("utf-8"))


def _snapshot_id(body: dict, revision: int) -> str:
    digest = hashlib.sha1(
        json.dumps(
            {"revision": int(revision), "body": body},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"arrangement-{int(revision)}-{digest}"


def _snapshot_chunk(
    clips: dict,
    *,
    snapshot_id: str,
    revision: int,
    chunk_index: int,
    chunk_count: int,
) -> dict:
    return {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "revision": int(revision),
        "chunk_index": int(chunk_index),
        "chunk_count": int(chunk_count),
        "clips": clips,
    }


def _chunk_fits(
    clips: dict,
    *,
    snapshot_id: str,
    revision: int,
    chunk_index: int,
    chunk_count: int,
    max_payload_bytes: int,
) -> bool:
    candidate = _snapshot_chunk(
        clips,
        snapshot_id=snapshot_id,
        revision=revision,
        chunk_index=chunk_index,
        chunk_count=chunk_count,
    )
    return _encoded_json_size(candidate) <= int(max_payload_bytes)


def _split_track_clip_pages(
    track_key: str,
    track_clips: list,
    *,
    snapshot_id: str,
    revision: int,
    first_chunk_index: int,
    chunk_count_hint: int,
    max_payload_bytes: int,
) -> list[dict]:
    pages = []
    current_page = []

    for clip in track_clips:
        candidate_page = current_page + [clip]
        if _chunk_fits(
            {track_key: candidate_page},
            snapshot_id=snapshot_id,
            revision=revision,
            chunk_index=first_chunk_index + len(pages),
            chunk_count=chunk_count_hint,
            max_payload_bytes=max_payload_bytes,
        ):
            current_page = candidate_page
            continue

        if current_page:
            pages.append({track_key: current_page})
            current_page = [clip]
            if _chunk_fits(
                {track_key: current_page},
                snapshot_id=snapshot_id,
                revision=revision,
                chunk_index=first_chunk_index + len(pages),
                chunk_count=chunk_count_hint,
                max_payload_bytes=max_payload_bytes,
            ):
                continue

        raise ValueError(
            f"Arrangement clip for track {track_key} exceeds the snapshot "
            "chunk byte budget."
        )

    if current_page or not track_clips:
        pages.append({track_key: current_page})
    return pages


def _snapshot_chunk_clip_maps(
    clips: dict,
    *,
    snapshot_id: str,
    revision: int,
    chunk_count_hint: int,
    max_payload_bytes: int,
) -> list[dict]:
    chunks = []
    current: dict = {}

    for track_key, track_clips in clips.items():
        candidate = dict(current)
        candidate[track_key] = track_clips
        if _chunk_fits(
            candidate,
            snapshot_id=snapshot_id,
            revision=revision,
            chunk_index=len(chunks),
            chunk_count=chunk_count_hint,
            max_payload_bytes=max_payload_bytes,
        ):
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = {}

        track_payload = {track_key: track_clips}
        if _chunk_fits(
            track_payload,
            snapshot_id=snapshot_id,
            revision=revision,
            chunk_index=len(chunks),
            chunk_count=chunk_count_hint,
            max_payload_bytes=max_payload_bytes,
        ):
            current = track_payload
            continue

        chunks.extend(_split_track_clip_pages(
            track_key,
            track_clips,
            snapshot_id=snapshot_id,
            revision=revision,
            first_chunk_index=len(chunks),
            chunk_count_hint=chunk_count_hint,
            max_payload_bytes=max_payload_bytes,
        ))

    if current:
        chunks.append(current)
    return chunks


def build_arrangement_snapshot_chunks(
    song,
    *,
    revision: int,
    max_payload_bytes: int = MAX_ARRANGEMENT_SNAPSHOT_CHUNK_BYTES,
) -> dict:
    """Build a manifest plus chunked Arrangement View clip payloads."""
    try:
        body = _snapshot_body(song)
    except ArrangementIdentityError as exc:
        return _error_payload("identity_unavailable", str(exc))
    except ArrangementNoteSignatureError as exc:
        return _error_payload("notes_unavailable", str(exc))

    revision = int(revision)
    max_payload_bytes = int(max_payload_bytes)
    snapshot_id = _snapshot_id(body, revision)
    chunk_count_hint = 0
    try:
        while True:
            clip_maps = _snapshot_chunk_clip_maps(
                body.get("clips", {}),
                snapshot_id=snapshot_id,
                revision=revision,
                chunk_count_hint=chunk_count_hint,
                max_payload_bytes=max_payload_bytes,
            )
            if len(clip_maps) == chunk_count_hint:
                break
            chunk_count_hint = len(clip_maps)
    except ValueError as exc:
        return _error_payload("chunk_too_large", str(exc))

    chunk_count = len(clip_maps)
    chunks = [
        _snapshot_chunk(
            chunk_clips,
            snapshot_id=snapshot_id,
            revision=revision,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
        )
        for chunk_index, chunk_clips in enumerate(clip_maps)
    ]
    for chunk in chunks:
        if _encoded_json_size(chunk) > max_payload_bytes:
            return _error_payload(
                "chunk_too_large",
                "Arrangement snapshot chunk exceeds the encoded byte budget."
            )

    manifest = {
        key: value
        for key, value in body.items()
        if key != "clips"
    }
    manifest.update({
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "snapshot_id": snapshot_id,
        "chunk_count": chunk_count,
    })
    return {
        "manifest": manifest,
        "chunks": chunks,
    }


def _metadata_changed(previous: dict, current: dict) -> bool:
    keys = (
        "track_names",
        "track_indices",
        "midi_tracks",
        "track_colors",
        "is_group_tracks",
        "group_parent_indices",
    )
    return any(previous.get(key) != current.get(key) for key in keys)


class ArrangementDeltaCache:
    """Keep Bridge-owned Arrangement state and produce revisioned deltas."""

    def __init__(self):
        self._revision = 0
        self._snapshot: dict | None = None
        self._snapshot_chunks: dict[str, list[dict]] = {}

    @property
    def revision(self) -> int:
        return self._revision

    def snapshot(self, song) -> dict:
        self._revision += 1
        snapshot = build_arrangement_snapshot(song, revision=self._revision)
        if snapshot.get("status") == "ok":
            self._snapshot = copy.deepcopy(snapshot)
        return snapshot

    def snapshot_manifest(self, song) -> dict:
        self._revision += 1
        chunked = build_arrangement_snapshot_chunks(
            song, revision=self._revision)
        if chunked.get("status") == "error":
            return chunked

        manifest = chunked["manifest"]
        chunks = chunked["chunks"]
        snapshot_id = manifest["snapshot_id"]
        self._snapshot_chunks = {snapshot_id: copy.deepcopy(chunks)}

        clips = {}
        for chunk in chunks:
            for track_key, track_clips in chunk.get("clips", {}).items():
                clips.setdefault(track_key, []).extend(track_clips)
        snapshot = copy.deepcopy(manifest)
        snapshot["clips"] = clips
        self._snapshot = snapshot
        return copy.deepcopy(manifest)

    def snapshot_chunk(self, snapshot_id, chunk_index) -> dict:
        snapshot_id = str(snapshot_id)
        chunks = self._snapshot_chunks.get(snapshot_id)
        if chunks is None:
            return _error_payload(
                "unknown_snapshot",
                "Arrangement snapshot is not available.",
            )

        try:
            index = int(chunk_index)
        except (TypeError, ValueError):
            return _error_payload(
                "invalid_chunk_index",
                "Arrangement snapshot chunk index is invalid.",
            )

        if index < 0 or index >= len(chunks):
            return _error_payload(
                "invalid_chunk_index",
                "Arrangement snapshot chunk index is out of range.",
            )
        return copy.deepcopy(chunks[index])

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
        except ArrangementNoteSignatureError as exc:
            return _error_payload("notes_unavailable", str(exc))

        previous = self._snapshot
        changes = []

        if _metadata_changed(previous, current):
            self._revision += 1
            full = copy.deepcopy(current)
            full.update({
                "status": "ok",
                "schema_version": SCHEMA_VERSION,
                "revision": self._revision,
            })
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
