"""Unit coverage for the diagnostic Application.View.zoom_view probe."""

import sys
import types


class _Component:
    def __init__(self, *args, **kwargs):
        pass

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


class _Song:
    def __init__(self):
        self.scenes = []
        self.tracks = []
        self.view = types.SimpleNamespace(
            selected_scene=None,
            selected_track=types.SimpleNamespace(
                devices=[],
                view=types.SimpleNamespace(selected_device=None),
            ),
        )


class _ApplicationView:
    def __init__(self):
        self.calls = []

    def zoom_view(self, direction, view_name, modifier_pressed):
        self.calls.append((direction, view_name, modifier_pressed))


def _install_live_stubs(app_view):
    ableton = types.ModuleType("ableton")
    ableton_v2 = types.ModuleType("ableton.v2")
    control_surface = types.ModuleType("ableton.v2.control_surface")
    component = types.ModuleType("ableton.v2.control_surface.component")
    component.Component = _Component
    osc_server = types.ModuleType("abletonosc.osc_server")
    osc_server.OSCServer = object

    live = types.ModuleType("Live")
    live.Application = types.ModuleType("Live.Application")
    live.Application.get_application = lambda: types.SimpleNamespace(view=app_view)

    originals = {
        name: sys.modules.get(name)
        for name in (
            "ableton",
            "ableton.v2",
            "ableton.v2.control_surface",
            "ableton.v2.control_surface.component",
            "Live",
            "Live.Application",
            "abletonosc.osc_server",
            "abletonosc.handler",
            "abletonosc.view",
        )
    }
    sys.modules["ableton"] = ableton
    sys.modules["ableton.v2"] = ableton_v2
    sys.modules["ableton.v2.control_surface"] = control_surface
    sys.modules["ableton.v2.control_surface.component"] = component
    sys.modules["abletonosc.osc_server"] = osc_server
    sys.modules["Live"] = live
    sys.modules["Live.Application"] = live.Application
    sys.modules.pop("abletonosc.handler", None)
    sys.modules.pop("abletonosc.view", None)
    return originals


def _restore_modules(originals):
    for name, module in originals.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def test_view_handler_exposes_zoom_view_diagnostic_probe():
    app_view = _ApplicationView()
    originals = _install_live_stubs(app_view)
    try:
        from abletonosc.view import ViewHandler

        manager = _Manager(_Song())
        ViewHandler(manager)

        result = manager.osc_server.handlers["/live/view/probe/zoom_view"](
            (3, "Arranger", 0, 4)
        )

        assert result == (4, 4, 0, "")
        assert app_view.calls == [
            (3, "Arranger", False),
            (3, "Arranger", False),
            (3, "Arranger", False),
            (3, "Arranger", False),
        ]
    finally:
        _restore_modules(originals)


def test_zoom_view_diagnostic_probe_reports_first_exception_and_stops():
    class FailingApplicationView(_ApplicationView):
        def zoom_view(self, direction, view_name, modifier_pressed):
            super().zoom_view(direction, view_name, modifier_pressed)
            if len(self.calls) == 2:
                raise RuntimeError("zoom boundary")

    app_view = FailingApplicationView()
    originals = _install_live_stubs(app_view)
    try:
        from abletonosc.view import ViewHandler

        manager = _Manager(_Song())
        ViewHandler(manager)

        result = manager.osc_server.handlers["/live/view/probe/zoom_view"](
            (3, "Arranger", 0, 4)
        )

        assert result == (4, 1, 1, "RuntimeError: zoom boundary")
        assert app_view.calls == [
            (3, "Arranger", False),
            (3, "Arranger", False),
        ]
    finally:
        _restore_modules(originals)
