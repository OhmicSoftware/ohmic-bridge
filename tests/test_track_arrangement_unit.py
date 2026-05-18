"""Unit coverage for Bridge track arrangement clip fallback endpoints."""

import sys
import types


class _Component:
    def __init__(self, *args, **kwargs):
        pass


class _OscServer:
    def __init__(self):
        self.handlers = {}

    def add_handler(self, address, callback):
        self.handlers[address] = callback


class _Manager:
    def __init__(self, song):
        self.song = song
        self.osc_server = _OscServer()


class _Song:
    def __init__(self, tracks):
        self.tracks = list(tracks)


class _Track:
    def __init__(self, clips):
        self.arrangement_clips = list(clips)
        self.clip_slots = []
        self.devices = []
        self.mixer_device = object()


class _Clip:
    def __init__(
        self,
        color=None,
        color_index=None,
        end_time=None,
        looping=None,
        loop_start=None,
        loop_end=None,
    ):
        self.color = color
        self.color_index = color_index
        self.end_time = end_time
        self.looping = looping
        self.loop_start = loop_start
        self.loop_end = loop_end


def _install_live_stubs():
    ableton = types.ModuleType("ableton")
    ableton_v2 = types.ModuleType("ableton.v2")
    control_surface = types.ModuleType("ableton.v2.control_surface")
    component = types.ModuleType("ableton.v2.control_surface.component")
    component.Component = _Component
    osc_server = types.ModuleType("abletonosc.osc_server")
    osc_server.OSCServer = object
    originals = {
        name: sys.modules.get(name)
        for name in (
            "ableton",
            "ableton.v2",
            "ableton.v2.control_surface",
            "ableton.v2.control_surface.component",
            "abletonosc.osc_server",
            "abletonosc.handler",
            "abletonosc.track",
        )
    }
    sys.modules["ableton"] = ableton
    sys.modules["ableton.v2"] = ableton_v2
    sys.modules["ableton.v2.control_surface"] = control_surface
    sys.modules["ableton.v2.control_surface.component"] = component
    sys.modules["abletonosc.osc_server"] = osc_server
    sys.modules.pop("abletonosc.handler", None)
    sys.modules.pop("abletonosc.track", None)
    return originals


def _restore_modules(originals):
    for name, module in originals.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def test_track_handler_exposes_arrangement_clip_colors_and_color_indices():
    originals = _install_live_stubs()
    try:
        from abletonosc.track import TrackHandler

        song = _Song([
            _Track([
                _Clip(0x112233, 7),
                _Clip(0x445566, 12),
            ])
        ])
        manager = _Manager(song)

        handler = TrackHandler(manager)
        handler.song = song

        assert manager.osc_server.handlers[
            "/live/track/get/arrangement_clips/color"
        ]((0,)) == (0, 0x112233, 0x445566)
        assert manager.osc_server.handlers[
            "/live/track/get/arrangement_clips/color_index"
        ]((0,)) == (0, 7, 12)
    finally:
        _restore_modules(originals)


def test_track_handler_exposes_arrangement_clip_loop_fallback_metadata():
    originals = _install_live_stubs()
    try:
        from abletonosc.track import TrackHandler

        song = _Song([
            _Track([
                _Clip(
                    end_time=16.0,
                    looping=True,
                    loop_start=4.0,
                    loop_end=12.0,
                ),
            ])
        ])
        manager = _Manager(song)

        handler = TrackHandler(manager)
        handler.song = song

        assert manager.osc_server.handlers[
            "/live/track/get/arrangement_clips/end_time"
        ]((0,)) == (0, 16.0)
        assert manager.osc_server.handlers[
            "/live/track/get/arrangement_clips/looping"
        ]((0,)) == (0, True)
        assert manager.osc_server.handlers[
            "/live/track/get/arrangement_clips/loop_start"
        ]((0,)) == (0, 4.0)
        assert manager.osc_server.handlers[
            "/live/track/get/arrangement_clips/loop_end"
        ]((0,)) == (0, 12.0)
    finally:
        _restore_modules(originals)
