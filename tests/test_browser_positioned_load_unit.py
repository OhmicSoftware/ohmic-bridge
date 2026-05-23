"""Unit tests for positioned Bridge browser loads."""

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


def _fresh_browser_module():
    _stub_ableton_modules()
    _stub_bridge_package()
    module = importlib.import_module(f"{PACKAGE_NAME}.abletonosc.browser")
    return importlib.reload(module)


class _Device:
    def __init__(self, name):
        self.name = name


class _SongView:
    def __init__(self):
        self.selected_track = None
        self.selected_device = None

    def select_device(self, device):
        self.selected_device = device


class _Browser:
    plugins = True

    def __init__(self, track, song_view):
        self.track = track
        self.song_view = song_view

    def load_item(self, target):
        if self.track.view.device_insert_mode == 1:
            index = self.track.devices.index(self.song_view.selected_device)
        elif self.track.view.device_insert_mode == 2:
            index = self.track.devices.index(self.song_view.selected_device) + 1
        else:
            index = len(self.track.devices)
        self.track.devices.insert(index, _Device(target.name))


def test_positioned_browser_load_sets_restores_mode_and_returns_chain(monkeypatch):
    browser_module = _fresh_browser_module()
    handler = browser_module.BrowserHandler.__new__(browser_module.BrowserHandler)
    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    track = types.SimpleNamespace(
        devices=[
            _Device("Pigments"),
            _Device("Pro-Q 4"),
            _Device("SPAN"),
        ],
        view=types.SimpleNamespace(device_insert_mode=2),
    )
    song_view = _SongView()
    browser = _Browser(track, song_view)
    target = types.SimpleNamespace(is_loadable=True, name="Pro-C 2")

    monkeypatch.setattr(
        browser_module,
        "_find_loadable_for_browser_category",
        lambda _browser, _item, _category: target,
    )
    handler.osc_server = _Server()
    handler.song = types.SimpleNamespace(tracks=[track], view=song_view)
    handler._get_browser = lambda: browser
    handler.init_api()

    reply = callbacks["/live/browser/load"](
        (0, "plugins", "VST3/FabFilter/Pro-C 2", "before", 2)
    )

    assert reply == (
        0,
        "plugins",
        "VST3/FabFilter/Pro-C 2",
        "before",
        2,
        "ok",
        2,
        "Pigments",
        "Pro-Q 4",
        "Pro-C 2",
        "SPAN",
    )
    assert track.view.device_insert_mode == 2
    assert song_view.selected_track is track
    assert song_view.selected_device.name == "SPAN"


def test_positioned_browser_load_rejects_missing_anchor(monkeypatch):
    browser_module = _fresh_browser_module()
    handler = browser_module.BrowserHandler.__new__(browser_module.BrowserHandler)
    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    handler.osc_server = _Server()
    handler.song = types.SimpleNamespace(tracks=[], view=_SongView())
    handler._get_browser = lambda: types.SimpleNamespace(plugins=True)
    handler.init_api()

    reply = callbacks["/live/browser/load"](
        (0, "plugins", "VST3/FabFilter/Pro-C 2", "before")
    )

    assert reply == (
        0,
        "plugins",
        "VST3/FabFilter/Pro-C 2",
        "before",
        -1,
        "error: before/after insert_mode requires device_index",
    )
