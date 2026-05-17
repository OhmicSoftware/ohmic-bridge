"""Unit tests for Bridge 0.4.0 destructive-operation acknowledgements."""

import importlib
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "Ohmic_Bridge"


def _stub_ableton_modules():
    ableton = sys.modules.setdefault("ableton", types.ModuleType("ableton"))
    v2 = sys.modules.setdefault("ableton.v2", types.ModuleType("ableton.v2"))
    control_surface = sys.modules.setdefault(
        "ableton.v2.control_surface",
        types.ModuleType("ableton.v2.control_surface"),
    )
    component = sys.modules.setdefault(
        "ableton.v2.control_surface.component",
        types.ModuleType("ableton.v2.control_surface.component"),
    )
    ableton.v2 = v2
    v2.control_surface = control_surface
    control_surface.component = component
    component.Component = getattr(component, "Component", type("Component", (), {}))
    control_surface.ControlSurface = getattr(
        control_surface,
        "ControlSurface",
        type("ControlSurface", (), {}),
    )

    if "_Framework" not in sys.modules:
        framework = types.ModuleType("_Framework")
        encoder = types.ModuleType("_Framework.EncoderElement")
        encoder.EncoderElement = type("EncoderElement", (), {})
        sys.modules["_Framework"] = framework
        sys.modules["_Framework.EncoderElement"] = encoder

    if "Live" not in sys.modules:
        live = types.ModuleType("Live")
        clip_mod = types.SimpleNamespace()
        clip_mod.MidiNoteSpecification = object
        live.Clip = clip_mod
        sys.modules["Live"] = live


def _stub_bridge_package():
    package = sys.modules.get(PACKAGE_NAME)
    if package is None:
        package = types.ModuleType(PACKAGE_NAME)
        package.__path__ = [str(ROOT)]
        sys.modules[PACKAGE_NAME] = package
    if str(ROOT) not in package.__path__:
        package.__path__.append(str(ROOT))


def _fresh_manager_module():
    _stub_ableton_modules()
    _stub_bridge_package()
    module = importlib.import_module(f"{PACKAGE_NAME}.manager")
    return importlib.reload(module)


def _fresh_clip_module():
    _stub_ableton_modules()
    _stub_bridge_package()
    module = importlib.import_module(f"{PACKAGE_NAME}.abletonosc.clip")
    return importlib.reload(module)


def _fresh_track_module():
    _stub_ableton_modules()
    _stub_bridge_package()
    module = importlib.import_module(f"{PACKAGE_NAME}.abletonosc.track")
    return importlib.reload(module)


def test_bridge_version_is_0_4_0():
    manager = _fresh_manager_module()
    assert manager.BRIDGE_VERSION == (0, 4, 0)


def test_ableton_stub_repairs_existing_incomplete_control_surface_module():
    saved = {
        name: sys.modules.get(name)
        for name in (
            "ableton",
            "ableton.v2",
            "ableton.v2.control_surface",
            "ableton.v2.control_surface.component",
        )
    }
    try:
        ableton = types.ModuleType("ableton")
        ableton.v2 = types.ModuleType("ableton.v2")
        ableton.v2.control_surface = types.ModuleType("ableton.v2.control_surface")
        sys.modules["ableton"] = ableton
        sys.modules["ableton.v2"] = ableton.v2
        sys.modules["ableton.v2.control_surface"] = ableton.v2.control_surface

        _stub_ableton_modules()

        assert hasattr(sys.modules["ableton.v2.control_surface"], "ControlSurface")
        assert hasattr(
            sys.modules["ableton.v2.control_surface.component"],
            "Component",
        )
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_clip_remove_notes_handler_returns_ok_ack():
    clip_module = _fresh_clip_module()
    handler = clip_module.ClipHandler.__new__(clip_module.ClipHandler)

    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    class _Clip:
        def __init__(self):
            self.removed_args = None

        def remove_notes_extended(self, pitch_start, pitch_span, time_start, time_span):
            self.removed_args = (pitch_start, pitch_span, time_start, time_span)

    class _Slot:
        def __init__(self):
            self.clip = _Clip()

    class _Track:
        def __init__(self):
            self.clip_slots = [_Slot()]

    handler.osc_server = _Server()
    handler.song = types.SimpleNamespace(tracks=[_Track()])
    handler._clip_notes_cache = []
    handler.init_api()

    reply = callbacks["/live/clip/remove/notes"]((0, 0, 60, 1, 0.0, 1.0))
    assert reply == (0, 0, "ok")
    assert handler.song.tracks[0].clip_slots[0].clip.removed_args == (60, 1, 0.0, 1.0)


def test_clip_remove_notes_by_id_handler_returns_ok_ack():
    clip_module = _fresh_clip_module()
    handler = clip_module.ClipHandler.__new__(clip_module.ClipHandler)
    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    class _Clip:
        def __init__(self):
            self.removed_ids = None

        def remove_notes_by_id(self, note_ids):
            self.removed_ids = tuple(note_ids)

    class _Slot:
        def __init__(self):
            self.clip = _Clip()

    class _Track:
        def __init__(self):
            self.clip_slots = [_Slot()]

    handler.osc_server = _Server()
    handler.song = types.SimpleNamespace(tracks=[_Track()])
    handler._clip_notes_cache = []
    handler.init_api()

    reply = callbacks["/live/clip/remove_notes_by_id"]((0, 0, 101, 102))
    assert reply == (0, 0, "ok")
    assert handler.song.tracks[0].clip_slots[0].clip.removed_ids == (101, 102)


def test_arrangement_clip_remove_notes_handler_returns_ok_ack():
    clip_module = _fresh_clip_module()
    handler = clip_module.ClipHandler.__new__(clip_module.ClipHandler)
    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    class _Clip:
        def __init__(self):
            self.removed_args = None

        def remove_notes_extended(self, pitch_start, pitch_span, time_start, time_span):
            self.removed_args = (pitch_start, pitch_span, time_start, time_span)

    class _Track:
        def __init__(self):
            self.arrangement_clips = [_Clip()]

    handler.osc_server = _Server()
    handler.song = types.SimpleNamespace(tracks=[_Track()])
    handler._clip_notes_cache = []
    handler.init_api()

    reply = callbacks["/live/arrangement_clip/remove/notes"]((0, 0, 60, 1, 0.0, 1.0))
    assert reply == (0, 0, "ok")
    assert handler.song.tracks[0].arrangement_clips[0].removed_args == (
        60,
        1,
        0.0,
        1.0,
    )


def test_arrangement_clip_get_notes_default_includes_pitch_127():
    clip_module = _fresh_clip_module()
    handler = clip_module.ClipHandler.__new__(clip_module.ClipHandler)
    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    class _Note:
        pitch = 127
        start_time = 0.0
        duration = 1.0
        velocity = 100
        mute = False
        probability = 1.0

    class _Clip:
        def __init__(self):
            self.queried_args = None

        def get_notes_extended(self, pitch_start, pitch_span, time_start, time_span):
            self.queried_args = (pitch_start, pitch_span, time_start, time_span)
            if pitch_start <= 127 < pitch_start + pitch_span:
                return [_Note()]
            return []

    class _Track:
        def __init__(self):
            self.arrangement_clips = [_Clip()]

    handler.osc_server = _Server()
    handler.song = types.SimpleNamespace(tracks=[_Track()])
    handler._clip_notes_cache = []
    handler.init_api()

    reply = callbacks["/live/arrangement_clip/get/notes"]((0, 0))

    assert reply == (0, 0, 127, 0.0, 1.0, 100, False, 1.0)
    assert handler.song.tracks[0].arrangement_clips[0].queried_args == (
        0,
        128,
        -8192,
        16384,
    )


def test_track_delete_device_handler_returns_ok_ack():
    track_module = _fresh_track_module()
    handler = track_module.TrackHandler.__new__(track_module.TrackHandler)
    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    class _Track:
        def __init__(self):
            self.deleted = []

        def delete_device(self, device_index):
            self.deleted.append(device_index)

    live_track = _Track()
    handler.osc_server = _Server()
    handler.song = types.SimpleNamespace(tracks=[live_track])
    handler.init_api()

    reply = callbacks["/live/track/delete_device"]((0, 2))
    assert reply == (0, 2, "ok")
    assert live_track.deleted == [2]
