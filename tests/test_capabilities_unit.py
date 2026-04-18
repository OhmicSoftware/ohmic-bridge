"""Unit tests for abletonosc/capabilities.py.

These are PURE UNIT TESTS — they do not require a running Ableton.
Instead, we inject a fake `Live` module into sys.modules before
importing capabilities, so the hasattr predicates run against our
controlled fakes. This lets us verify every probe branch
independently.
"""
import sys
import types
import importlib
import pytest


@pytest.fixture
def fake_live_modern():
    """Build a fake `Live` module tree that looks like modern Ableton 12
    — extended MIDI note API present, all undocumented properties
    present. Returns the Live module; the fixture restores sys.modules
    on teardown."""

    def build():
        live = types.ModuleType("Live")
        live.Clip = types.ModuleType("Live.Clip")
        live.Clip.Clip = type("Clip", (), {
            "get_notes_extended": lambda *a: None,
            "add_new_notes": lambda *a: None,
            "remove_notes_extended": lambda *a: None,
            "remove_notes_by_id": lambda *a: None,
            "automation_envelope": lambda *a: None,
            "clear_envelope": lambda *a: None,
        })
        live.Clip.MidiNoteSpecification = type("MidiNoteSpecification", (), {})
        live.Track = types.ModuleType("Live.Track")
        live.Track.Track = type("Track", (), {
            "arrangement_clips": [],
            "create_midi_clip": lambda *a: None,
            "delete_clip": lambda *a: None,
        })
        live.ClipSlot = types.ModuleType("Live.ClipSlot")
        live.ClipSlot.ClipSlot = type("ClipSlot", (), {
            "duplicate_clip_to": lambda *a: None,
        })
        live.Song = types.ModuleType("Live.Song")
        live.Song.Song = type("Song", (), {
            "root_note": 0,
            "scale_name": "",
            "cue_points": [],
            "add_current_song_time_listener": lambda *a: None,
        })
        live.Scene = types.ModuleType("Live.Scene")
        live.Scene.Scene = type("Scene", (), {
            "tempo": 120.0,
            "tempo_enabled": False,
            "time_signature_numerator": 4,
            "time_signature_denominator": 4,
        })
        live.DeviceParameter = types.ModuleType("Live.DeviceParameter")
        live.DeviceParameter.DeviceParameter = type("DeviceParameter", (), {
            "str_for_value": lambda *a: "",
        })
        fake_app = type("App", (), {"browser": object()})()
        live.Application = types.ModuleType("Live.Application")
        live.Application.get_application = lambda: fake_app
        return live

    live = build()
    saved = {}
    for name in (
        "Live", "Live.Clip", "Live.Track", "Live.ClipSlot",
        "Live.Song", "Live.Scene", "Live.DeviceParameter",
        "Live.Application",
    ):
        if name in sys.modules:
            saved[name] = sys.modules[name]
    sys.modules["Live"] = live
    sys.modules["Live.Clip"] = live.Clip
    sys.modules["Live.Track"] = live.Track
    sys.modules["Live.ClipSlot"] = live.ClipSlot
    sys.modules["Live.Song"] = live.Song
    sys.modules["Live.Scene"] = live.Scene
    sys.modules["Live.DeviceParameter"] = live.DeviceParameter
    sys.modules["Live.Application"] = live.Application

    yield live

    for name in (
        "Live", "Live.Clip", "Live.Track", "Live.ClipSlot",
        "Live.Song", "Live.Scene", "Live.DeviceParameter",
        "Live.Application",
    ):
        if name in saved:
            sys.modules[name] = saved[name]
        else:
            sys.modules.pop(name, None)
    sys.modules.pop("abletonosc.capabilities", None)


def _import_fresh_capabilities():
    # sys.path is already set by D:\dev\Ohmic-Bridge\conftest.py so this
    # helper no longer needs to manipulate it — keeps the tests portable
    # between machines (Bob's D:\dev, Steve's C:\Users\...).
    if "abletonosc.capabilities" in sys.modules:
        del sys.modules["abletonosc.capabilities"]
    from abletonosc import capabilities
    importlib.reload(capabilities)
    capabilities.reset_for_testing()
    return capabilities


def test_probe_all_buckets_true_on_modern_ableton(fake_live_modern):
    capabilities = _import_fresh_capabilities()
    result = capabilities.probe_capabilities()
    assert result["clip_notes_rw"] is True
    assert result["clip_automation_envelopes"] is True
    assert result["arrangement_clips"] is True
    assert result["clip_slot_duplicate"] is True
    assert result["song_scale_properties"] is True
    assert result["scene_tempo"] is True
    assert result["scene_time_signature"] is True
    assert result["device_parameter_value_strings"] is True
    assert result["song_cue_points"] is True
    assert result["song_beat_listener"] is True
    assert result["browser"] is True


def test_clip_note_probability_bucket_no_longer_exists(fake_live_modern):
    """The ``clip_note_probability`` bucket was a sibling of
    ``clip_notes_rw`` during the dual-path era. After the legacy
    fallback was removed, probability is always available whenever the
    extended API is — which is exactly what ``clip_notes_rw`` now
    represents. Keeping both would double-count the same capability
    and create a drift risk in the message taxonomy, so the sub-bucket
    is gone."""
    capabilities = _import_fresh_capabilities()
    result = capabilities.probe_capabilities()
    assert "clip_note_probability" not in result


def test_probe_is_cached(fake_live_modern):
    capabilities = _import_fresh_capabilities()
    first = capabilities.probe_capabilities()
    second = capabilities.probe_capabilities()
    assert first is second


def test_clip_notes_rw_requires_full_extended_api(fake_live_modern):
    """Every piece of the extended MIDI note surface must be present —
    ``get_notes_extended``, ``add_new_notes``, ``remove_notes_extended``,
    and ``MidiNoteSpecification``. Missing any one forces the bucket to
    false so Ohmic blocks note-touching tools cleanly instead of
    partially erroring at call time."""
    delattr(fake_live_modern.Clip.Clip, "remove_notes_extended")
    capabilities = _import_fresh_capabilities()
    result = capabilities.probe_capabilities()
    assert result["clip_notes_rw"] is False


def test_probe_returns_false_when_extended_api_is_missing(fake_live_modern):
    for attr in (
        "get_notes_extended", "add_new_notes", "remove_notes_extended",
    ):
        if hasattr(fake_live_modern.Clip.Clip, attr):
            delattr(fake_live_modern.Clip.Clip, attr)
    capabilities = _import_fresh_capabilities()
    result = capabilities.probe_capabilities()
    assert result["clip_notes_rw"] is False


def test_probe_catches_exception_per_bucket(fake_live_modern):
    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("boom")
    fake_live_modern.Clip.Clip = Boom()
    capabilities = _import_fresh_capabilities()
    result = capabilities.probe_capabilities()
    assert result["clip_notes_rw"] is False
    assert result["browser"] is True
