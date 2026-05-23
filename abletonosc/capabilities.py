"""Connection-time capability probe.

Reports which undocumented Live Object Model APIs are present on the
current Ableton install. The Ohmic MCP server calls
/live/api/ohmic/capabilities at connect time, caches the result, and
uses it to gate MCP tools that depend on undocumented LOM surface.

See D:\\dev\\Ohmic\\documentation\\BRIDGE_LOM_AUDIT.md for the full
bucket taxonomy and the audit discipline rule: any PR that adds a
Bridge handler touching undocumented LOM must add the relevant bucket
here and in BRIDGE_LOM_AUDIT.md.
"""
import logging

import Live

logger = logging.getLogger("abletonosc")


CAPABILITY_GROUPS = {
    "clip_notes_rw": [
        # Extended MIDI note API — the only surface Ohmic uses. Present
        # in Live 11.0 and later. If any of these is missing the bucket
        # is false and every note-touching tool returns a clean
        # capability error rather than calling through.
        lambda: hasattr(Live.Clip.Clip, "get_notes_extended"),
        lambda: hasattr(Live.Clip.Clip, "add_new_notes"),
        lambda: hasattr(Live.Clip.Clip, "remove_notes_extended"),
        lambda: hasattr(Live.Clip, "MidiNoteSpecification"),
    ],
    "clip_automation_envelopes": [
        lambda: hasattr(Live.Clip.Clip, "automation_envelope"),
        lambda: hasattr(Live.Clip.Clip, "clear_envelope"),
    ],
    "arrangement_clips": [
        lambda: hasattr(Live.Track.Track, "arrangement_clips"),
        lambda: hasattr(Live.Track.Track, "create_midi_clip"),
        lambda: hasattr(Live.Track.Track, "delete_clip"),
    ],
    "arrangement_deltas": [
        lambda: hasattr(Live.Track.Track, "arrangement_clips"),
    ],
    "arrangement_snapshot_chunks": [
        lambda: hasattr(Live.Track.Track, "arrangement_clips"),
    ],
    "clip_slot_duplicate": [
        lambda: hasattr(Live.ClipSlot.ClipSlot, "duplicate_clip_to"),
    ],
    "song_scale_properties": [
        lambda: hasattr(Live.Song.Song, "root_note"),
        lambda: hasattr(Live.Song.Song, "scale_name"),
    ],
    "scene_tempo": [
        lambda: hasattr(Live.Scene.Scene, "tempo"),
        lambda: hasattr(Live.Scene.Scene, "tempo_enabled"),
    ],
    "scene_time_signature": [
        lambda: hasattr(Live.Scene.Scene, "time_signature_numerator"),
        lambda: hasattr(Live.Scene.Scene, "time_signature_denominator"),
    ],
    "device_parameter_value_strings": [
        lambda: hasattr(Live.DeviceParameter.DeviceParameter, "str_for_value"),
    ],
    "song_cue_points": [
        lambda: hasattr(Live.Song.Song, "cue_points"),
    ],
    "song_beat_listener": [
        lambda: hasattr(Live.Song.Song, "add_current_song_time_listener"),
    ],
    "song_device_move": [
        lambda: hasattr(Live.Song.Song, "move_device"),
    ],
    "browser": [
        lambda: hasattr(Live.Application.get_application(), "browser"),
    ],
}

_probe_result = None


def probe_capabilities():
    """Compute and cache bucket state. LOM availability does not change
    within a Live session, so subsequent calls return the cached dict."""
    global _probe_result
    if _probe_result is not None:
        return _probe_result

    result = {}
    for bucket, checks in CAPABILITY_GROUPS.items():
        try:
            result[bucket] = all(check() for check in checks)
        except Exception:
            logger.exception("capability probe failed for %s", bucket)
            result[bucket] = False

    _probe_result = result
    logger.info("Capability probe result: %s", result)
    return result


def probe_arrangement_deltas(song) -> bool:
    """Return whether optimized arrangement deltas can identify clips.

    The `_live_ptr` identity we use is an instance attribute, so the
    class-level capability table can only prove that arrangement clips
    exist. When clips are present, inspect them directly. Empty
    arrangements are supported because there is nothing to identify yet;
    the snapshot endpoint will still validate identity whenever clips
    later appear.
    """
    try:
        if not probe_capabilities().get("arrangement_deltas", False):
            return False
        for track in list(getattr(song, "tracks", [])):
            try:
                clips = list(getattr(track, "arrangement_clips", []))
            except Exception:
                continue
            for clip in clips:
                value = getattr(clip, "_live_ptr", None)
                if value is None:
                    return False
                int(value)
        return True
    except Exception:
        logger.exception("arrangement delta capability probe failed")
        return False


def reset_for_testing():
    """Clear the cache. Tests-only helper."""
    global _probe_result
    _probe_result = None
