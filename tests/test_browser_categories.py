import importlib
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "Ohmic_Bridge"
MODULE_ROOTS = (
    "Live",
    "ableton",
    PACKAGE_NAME,
)


def _matches_fixture_module(name):
    return any(name == root or name.startswith(f"{root}.") for root in MODULE_ROOTS)


def _snapshot_modules():
    return {
        module_name: module
        for module_name, module in sys.modules.items()
        if _matches_fixture_module(module_name)
    }


def _restore_modules(snapshot):
    for module_name in tuple(sys.modules):
        if _matches_fixture_module(module_name) and module_name not in snapshot:
            sys.modules.pop(module_name, None)
    sys.modules.update(snapshot)


@pytest.fixture
def browser_module():
    saved_modules = _snapshot_modules()

    live = types.ModuleType("Live")
    application = types.ModuleType("Live.Application")
    application.get_application = lambda: types.SimpleNamespace(browser=object())
    live.Application = application

    ableton = types.ModuleType("ableton")
    v2 = types.ModuleType("ableton.v2")
    control_surface = types.ModuleType("ableton.v2.control_surface")
    component = types.ModuleType("ableton.v2.control_surface.component")
    component.Component = type("Component", (), {})
    ableton.v2 = v2
    v2.control_surface = control_surface
    control_surface.component = component

    bridge_package = types.ModuleType(PACKAGE_NAME)
    bridge_package.__path__ = [str(ROOT)]

    sys.modules["Live"] = live
    sys.modules["Live.Application"] = application
    sys.modules["ableton"] = ableton
    sys.modules["ableton.v2"] = v2
    sys.modules["ableton.v2.control_surface"] = control_surface
    sys.modules["ableton.v2.control_surface.component"] = component
    sys.modules[PACKAGE_NAME] = bridge_package

    try:
        yield importlib.import_module(f"{PACKAGE_NAME}.abletonosc.browser")
    finally:
        _restore_modules(saved_modules)


def test_category_map_exposes_ohmic_keys(browser_module):
    assert browser_module.CATEGORY_MAP["instruments"] == "instruments"
    assert browser_module.CATEGORY_MAP["plugins"] == "plugins"
    assert browser_module.CATEGORY_MAP["instrument_racks"] == "user_library"
    assert browser_module.CATEGORY_MAP["drum_racks"] == "user_library"
    assert browser_module.CATEGORY_MAP["audio_effect_racks"] == "user_library"
    assert browser_module.CATEGORY_MAP["midi_effect_racks"] == "user_library"
    assert browser_module.CATEGORY_MAP["ableton_presets"] == "user_library"
    assert browser_module.CATEGORY_MAP["plugin_presets"] == "user_library"
    assert browser_module.CATEGORY_MAP["user_library_max_for_live"] == "max_for_live"
    assert browser_module.CATEGORY_MAP["max_for_live"] == "max_for_live"


class _BrowserStub:
    def __init__(self, **attrs):
        for name, value in attrs.items():
            setattr(self, name, value)


def test_max_for_live_capability_requires_browser_support(browser_module):
    supported = _BrowserStub(max_for_live=object())
    unsupported = _BrowserStub()
    assert browser_module._max_for_live_supported(supported) is True
    assert browser_module._max_for_live_supported(unsupported) is False


def test_max_for_live_unsupported_error_takes_precedence(browser_module):
    browser = _BrowserStub()
    assert browser_module._max_for_live_unsupported_error("max_for_live", browser) == (
        browser_module.MAX_FOR_LIVE_UNSUPPORTED_ERROR,
    )


# _category_for_user_library_path is an intentional internal classification helper contract for fast unit coverage.
def test_classifies_adg_rack_paths(browser_module):
    cases = {
        "Presets/Instruments/Instrument Rack/My Bass.adg": "instrument_racks",
        "Presets/Instruments/Drum Rack/808 Kit.adg": "drum_racks",
        "Presets/Audio Effects/Audio Effect Rack/Bus Glue.adg": "audio_effect_racks",
        "Presets/MIDI Effects/MIDI Effect Rack/Chord Tool.adg": "midi_effect_racks",
    }
    for path, expected in cases.items():
        assert browser_module._category_for_user_library_path(path) == expected


def test_classifies_preset_paths_by_extension(browser_module):
    assert (
        browser_module._category_for_user_library_path(
            "Presets/Instruments/Operator/Soft Lead.adv"
        )
        == "ableton_presets"
    )
    assert (
        browser_module._category_for_user_library_path(
            "Presets/Plug-ins/VST3/Arturia/Pigments/LushPad.vstpreset"
        )
        == "plugin_presets"
    )
    assert (
        browser_module._category_for_user_library_path(
            "Presets/Plug-ins/AU/FabFilter/Pro-Q 4/Clean EQ.aupreset"
        )
        == "plugin_presets"
    )


def test_classifies_max_for_live_paths(browser_module):
    assert (
        browser_module._category_for_user_library_path(
            "Presets/Max for Live/Max Audio Effect/LFO.amxd"
        )
        == "user_library_max_for_live"
    )


def test_unknown_or_unsupported_user_library_path_is_none(browser_module):
    assert browser_module._category_for_user_library_path("Samples/Kick.wav") is None
    assert browser_module._category_for_user_library_path("Clips/Idea.alc") is None


def test_new_preset_categories_skip_device_count_verification(browser_module):
    assert browser_module._is_preset_category("ableton_presets") is True
    assert browser_module._is_preset_category("plugin_presets") is True


def test_non_preset_categories_verify_device_count(browser_module):
    assert browser_module._is_preset_category("instrument_racks") is False
    assert browser_module._is_preset_category("plugins") is False


class _FakeBrowserItem:
    def __init__(self, name, *, loadable=False, children=()):
        self.name = name
        self.is_loadable = loadable
        self.children = list(children)

    @property
    def is_folder(self):
        return bool(self.children)


def _fake_user_library_tree():
    return [
        _FakeBrowserItem("Presets", children=[
            _FakeBrowserItem("Instruments", children=[
                _FakeBrowserItem("Instrument Rack", children=[
                    _FakeBrowserItem("Bass Rack.adg", loadable=True),
                ]),
                _FakeBrowserItem("Operator", children=[
                    _FakeBrowserItem("Lead.adv", loadable=True),
                ]),
            ]),
        ]),
    ]


def _fake_duplicate_name_tree():
    return [
        _FakeBrowserItem("Presets", children=[
            _FakeBrowserItem("Instruments", children=[
                _FakeBrowserItem("Operator", children=[
                    _FakeBrowserItem("SharedName.adg", loadable=True),
                ]),
                _FakeBrowserItem("Instrument Rack", children=[
                    _FakeBrowserItem("SharedName.adg", loadable=True),
                ]),
            ]),
        ]),
    ]


def test_collect_loadable_filters_user_library_categories(browser_module):
    results = []
    browser_module._collect_loadable(
        _fake_user_library_tree(), "", 0, results, "instrument_racks"
    )
    assert results == ["Presets/Instruments/Instrument Rack/Bass Rack.adg"]


def test_collect_loadable_leaves_non_user_library_categories_unfiltered(browser_module):
    results = []
    browser_module._collect_loadable(
        [_FakeBrowserItem("Operator", loadable=True)], "", 0, results, "instruments"
    )
    assert results == ["Operator"]


def test_collect_loadable_keeps_installed_max_for_live_unfiltered(browser_module):
    browser_module._USER_LIBRARY_MAX_FOR_LIVE_STEMS = {"User LFO"}
    results = []
    browser_module._collect_loadable(
        [_FakeBrowserItem("LFO", loadable=True)], "", 0, results, "max_for_live"
    )
    assert results == ["LFO"]


def test_collect_loadable_filters_user_library_max_for_live(browser_module):
    browser_module._USER_LIBRARY_MAX_FOR_LIVE_STEMS = {"IMG"}
    tree = [
        _FakeBrowserItem("Max Audio Effect", children=[
            _FakeBrowserItem("IMG", loadable=True),
            _FakeBrowserItem("Max Audio Effect", loadable=True),
        ]),
        _FakeBrowserItem("Max MIDI Effect", children=[
            _FakeBrowserItem("Stepic", loadable=True),
        ]),
    ]
    results = []
    browser_module._collect_loadable(
        tree, "", 0, results, "user_library_max_for_live"
    )
    assert results == ["Max Audio Effect/IMG"]


def test_collect_category_items_deduplicates_user_max_for_live_by_stem(browser_module):
    browser_module._USER_LIBRARY_MAX_FOR_LIVE_STEMS = {"IMG"}
    browser = _BrowserStub(
        max_for_live=[
            _FakeBrowserItem("Max Audio Effect", children=[
                _FakeBrowserItem("IMG", loadable=True),
            ]),
        ],
        user_library=[
            _FakeBrowserItem("Presets", children=[
                _FakeBrowserItem("Audio Effects", children=[
                    _FakeBrowserItem("Max Audio Effect", children=[
                        _FakeBrowserItem("Imported", children=[
                            _FakeBrowserItem("IMG.amxd", loadable=True),
                        ]),
                    ]),
                ]),
            ]),
        ],
    )

    assert browser_module._collect_category_items(
        browser, "user_library_max_for_live"
    ) == ["Max Audio Effect/IMG"]


def test_collect_installed_max_for_live_excludes_user_library_stems(browser_module):
    browser_module._USER_LIBRARY_MAX_FOR_LIVE_STEMS = {"IMG"}
    tree = [
        _FakeBrowserItem("Max Audio Effect", children=[
            _FakeBrowserItem("IMG", loadable=True),
            _FakeBrowserItem("Max Audio Effect", loadable=True),
        ]),
    ]
    results = []
    browser_module._collect_loadable(tree, "", 0, results, "max_for_live")
    assert results == ["Max Audio Effect/Max Audio Effect"]


def test_collect_composite_installed_max_for_live_uses_audio_midi_roots(browser_module):
    browser_module._USER_LIBRARY_MAX_FOR_LIVE_STEMS = set()
    browser_module._INSTALLED_MAX_FOR_LIVE_STEMS = {"LFO", "Shaper MIDI"}
    browser = _BrowserStub(
        max_for_live=[
            _FakeBrowserItem("Max Audio Effect", children=[
                _FakeBrowserItem("Max Audio Effect", loadable=True),
            ]),
        ],
        audio_effects=[
            _FakeBrowserItem("Audio Effects", children=[
                _FakeBrowserItem("LFO", loadable=True),
                _FakeBrowserItem("Hybrid Reverb", loadable=True),
            ]),
        ],
        midi_effects=[
            _FakeBrowserItem("MIDI Effects", children=[
                _FakeBrowserItem("Shaper MIDI", loadable=True),
                _FakeBrowserItem("Scale", loadable=True),
            ]),
        ],
    )

    assert browser_module._collect_category_items(browser, "max_for_live") == [
        "Max Audio Effect/Max Audio Effect",
        "Audio Effects/LFO",
        "MIDI Effects/Shaper MIDI",
    ]


def test_find_user_library_max_for_live_uses_max_for_live_tree(browser_module):
    browser_module._USER_LIBRARY_MAX_FOR_LIVE_STEMS = {"IMG"}
    browser = _BrowserStub(max_for_live=[
        _FakeBrowserItem("Max Audio Effect", children=[
            _FakeBrowserItem("IMG", loadable=True),
        ]),
    ])

    target = browser_module._find_loadable_for_browser_category(
        browser, "IMG", "user_library_max_for_live"
    )

    assert target is not None
    assert target.name == "IMG"


def test_find_loadable_for_category_rejects_mismatched_path(browser_module):
    target = browser_module._find_loadable_for_category(
        _fake_user_library_tree(),
        "Presets/Instruments/Operator/Lead.adv",
        "instrument_racks",
    )
    assert target is None


def test_find_loadable_for_category_allows_matching_path(browser_module):
    target = browser_module._find_loadable_for_category(
        _fake_user_library_tree(),
        "Presets/Instruments/Instrument Rack/Bass Rack.adg",
        "instrument_racks",
    )
    assert target is not None
    assert target.name == "Bass Rack.adg"


def test_find_loadable_for_category_continues_past_mismatched_bare_name(browser_module):
    target = browser_module._find_loadable_for_category(
        _fake_duplicate_name_tree(), "SharedName.adg", "instrument_racks"
    )
    assert target is not None
    assert target.name == "SharedName.adg"
