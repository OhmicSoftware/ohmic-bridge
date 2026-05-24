import importlib
import json
from pathlib import Path
import subprocess
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
    assert browser_module.CATEGORY_MAP["audio_effects"] == "audio_effects"
    assert browser_module.CATEGORY_MAP["midi_effects"] == "midi_effects"
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


def test_resolve_user_library_browser_path_rejects_unsafe_paths(browser_module, tmp_path):
    root = tmp_path / "User Library"
    root.mkdir()

    unsafe_paths = [
        "../outside.adg",
        "/tmp/outside.adg",
        r"C:\outside.adg",
        r"\\server\share\outside.adg",
    ]

    for browser_path in unsafe_paths:
        result = browser_module._resolve_user_library_file(
            root,
            browser_path,
            "instrument_racks",
        )
        assert result is None


def test_resolve_user_library_browser_path_returns_existing_supported_file(
    browser_module, tmp_path
):
    root = tmp_path / "User Library"
    target = root / "Presets" / "Instruments" / "Instrument Rack" / "Warm Pad.adg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")

    result = browser_module._resolve_user_library_file(
        root,
        "Presets/Instruments/Instrument Rack/Warm Pad.adg",
        "instrument_racks",
    )

    assert result == target


def test_resolve_user_library_browser_path_rejects_symlink_escape(
    browser_module, tmp_path
):
    root = tmp_path / "User Library"
    outside = tmp_path / "Outside"
    outside.mkdir()
    escaped = outside / "Escaped.adg"
    escaped.write_bytes(b"payload")
    link = root / "Presets" / "Instruments" / "Instrument Rack"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip("could not create symlink: %s" % exc)

    result = browser_module._resolve_user_library_file(
        root,
        "Presets/Instruments/Instrument Rack/Escaped.adg",
        "instrument_racks",
    )

    assert result is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction escape")
def test_resolve_user_library_browser_path_rejects_windows_junction_escape(
    browser_module, tmp_path
):
    root = tmp_path / "User Library"
    outside = tmp_path / "Outside"
    outside.mkdir()
    escaped = outside / "Escaped.adg"
    escaped.write_bytes(b"payload")
    junction = root / "Presets" / "Instruments" / "Instrument Rack"
    junction.parent.mkdir(parents=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("could not create junction: %s" % result.stderr)

    resolved = browser_module._resolve_user_library_file(
        root,
        "Presets/Instruments/Instrument Rack/Escaped.adg",
        "instrument_racks",
    )

    assert resolved is None


def test_resolve_user_library_browser_path_rejects_category_mismatch(
    browser_module, tmp_path
):
    root = tmp_path / "User Library"
    target = root / "Presets" / "Instruments" / "Operator" / "Lead.adv"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")

    result = browser_module._resolve_user_library_file(
        root,
        "Presets/Instruments/Operator/Lead.adv",
        "instrument_racks",
    )

    assert result is None


def test_metadata_marks_missing_supported_user_library_file_stale(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    root.mkdir()
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="instrument_racks",
        offset=0,
        limit=10,
        paths=["Presets/Instruments/Instrument Rack/Missing.adg"],
    )

    item = payload["items"][0]
    assert item["browser_path"] == "Presets/Instruments/Instrument Rack/Missing.adg"
    assert item["name"] == "Missing.adg"
    assert item["metadata_status"] == "stale_missing_file"
    assert item["file_backed_expected"] is True
    assert item["file_exists"] is False


def test_max_for_live_display_path_resolves_unambiguous_amxd_stem(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    amxd = (
        root
        / "Presets"
        / "Audio Effects"
        / "Max Audio Effect"
        / "Ohmic Metadata Test Max Device.amxd"
    )
    amxd.parent.mkdir(parents=True)
    amxd.write_bytes(b"payload")
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))
    monkeypatch.setattr(
        browser_module.browser_metadata,
        "metadata_for_file",
        lambda path, category, browser_path, hash_budget=None: {
            "browser_path": browser_path,
            "name": Path(path).name,
            "extension": ".amxd",
            "category": category,
            "size": 7,
            "mtime_ns": 123,
            "file_id": "fileid:test",
            "sha256": "abc",
            "sha256_status": "ready",
        },
    )

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="user_library_max_for_live",
        offset=0,
        limit=10,
        paths=["Max Audio Effect/Ohmic Metadata Test Max Device"],
    )

    item = payload["items"][0]
    assert item["metadata_status"] == "file_backed"
    assert item["file_backed_expected"] is True
    assert item["file_exists"] is True
    assert item["extension"] == ".amxd"
    assert item["file_id"] == "fileid:test"
    assert str(root) not in repr(item)


def test_max_for_live_ambiguous_display_stem_marks_ambiguous(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    first = root / "A" / "IMG.amxd"
    second = root / "B" / "IMG.amxd"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"payload")
    second.write_bytes(b"payload")
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="user_library_max_for_live",
        offset=0,
        limit=10,
        paths=["Max Audio Effect/IMG"],
    )

    item = payload["items"][0]
    assert item["metadata_status"] == "ambiguous_file_match"
    assert item["file_backed_expected"] is False
    assert item["file_exists"] is False
    assert "file_id" not in item
    assert str(root) not in repr(item)


def test_max_for_live_no_match_display_path_remains_path_only(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    root.mkdir()
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="user_library_max_for_live",
        offset=0,
        limit=10,
        paths=["Max Audio Effect/Display Only"],
    )

    item = payload["items"][0]
    assert item["metadata_status"] == "path_only"
    assert item["file_backed_expected"] is False
    assert item["file_exists"] is False


def test_max_for_live_metadata_page_scans_amxd_files_once_per_page(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    folder = root / "Presets" / "Audio Effects" / "Max Audio Effect"
    folder.mkdir(parents=True)
    for name in ("One.amxd", "Two.amxd", "Three.amxd"):
        (folder / name).write_bytes(b"payload")
    calls = []
    real_rglob = browser_module.Path.rglob

    def counting_rglob(path, pattern):
        calls.append((path, pattern))
        return real_rglob(path, pattern)

    monkeypatch.setattr(browser_module.Path, "rglob", counting_rglob)
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="user_library_max_for_live",
        offset=0,
        limit=10,
        paths=[
            "Max Audio Effect/One",
            "Max Audio Effect/Two",
            "Max Audio Effect/Three",
        ],
    )

    assert [item["metadata_status"] for item in payload["items"]] == [
        "file_backed",
        "file_backed",
        "file_backed",
    ]
    assert calls == [(root, "*.amxd")]


def test_file_backed_item_without_file_id_is_hash_only(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    target = root / "Presets" / "Instruments" / "Instrument Rack" / "Warm Pad.adg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))
    monkeypatch.setattr(
        browser_module.browser_metadata,
        "metadata_for_file",
        lambda path, category, browser_path, hash_budget=None: {
            "browser_path": browser_path,
            "name": Path(path).name,
            "extension": ".adg",
            "category": category,
            "size": 7,
            "mtime_ns": 123,
            "file_id": None,
            "sha256": "abc",
            "sha256_status": "ready",
        },
    )

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="instrument_racks",
        offset=0,
        limit=10,
        paths=["Presets/Instruments/Instrument Rack/Warm Pad.adg"],
    )

    item = payload["items"][0]
    assert item["metadata_status"] == "hash_only"
    assert item["file_backed_expected"] is True
    assert item["file_exists"] is True
    assert item["file_id"] is None


def test_max_for_live_stem_item_without_file_id_is_hash_only(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    target = root / "Presets" / "Audio Effects" / "Max Audio Effect" / "IMG.amxd"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))
    monkeypatch.setattr(
        browser_module.browser_metadata,
        "metadata_for_file",
        lambda path, category, browser_path, hash_budget=None: {
            "browser_path": browser_path,
            "name": Path(path).name,
            "extension": ".amxd",
            "category": category,
            "size": 7,
            "mtime_ns": 123,
            "file_id": None,
            "sha256": "abc",
            "sha256_status": "ready",
        },
    )

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="user_library_max_for_live",
        offset=0,
        limit=10,
        paths=["Max Audio Effect/IMG"],
    )

    item = payload["items"][0]
    assert item["metadata_status"] == "hash_only"
    assert item["file_backed_expected"] is True
    assert item["file_exists"] is True
    assert item["file_id"] is None


def test_metadata_page_payload_stays_below_page_byte_limit(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    rack_dir = root / "Presets" / "Instruments" / "Instrument Rack"
    rack_dir.mkdir(parents=True)
    paths = []
    for index in range(browser_module.MAX_METADATA_PAGE_LIMIT + 5):
        browser_path = "Presets/Instruments/Instrument Rack/Preset %02d.adg" % index
        (rack_dir / ("Preset %02d.adg" % index)).write_bytes(b"payload")
        paths.append(browser_path)
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="instrument_racks",
        offset=0,
        limit=browser_module.MAX_METADATA_PAGE_LIMIT,
        paths=paths,
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    assert len(payload["items"]) == browser_module.MAX_METADATA_PAGE_LIMIT
    assert len(encoded.encode("utf-8")) <= browser_module.MAX_METADATA_PAGE_BYTES


def test_metadata_items_sanitize_absolute_paths_for_path_only_stale_and_ambiguous(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    (root / "A").mkdir(parents=True)
    (root / "B").mkdir(parents=True)
    (root / "A" / "IMG.amxd").write_bytes(b"payload")
    (root / "B" / "IMG.amxd").write_bytes(b"payload")
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))
    absolute_inputs = [
        r"C:\outside\Preset.adg",
        r"C:outside\Preset.adg",
        r"C:Preset.adg",
        "/tmp/outside/Preset.adg",
        r"\\server\share\Preset.adg",
    ]

    path_only = browser_module._metadata_for_category_items(
        browser=object(),
        category="user_library_max_for_live",
        offset=0,
        limit=10,
        paths=absolute_inputs,
    )
    stale = browser_module._metadata_for_category_items(
        browser=object(),
        category="instrument_racks",
        offset=0,
        limit=10,
        paths=["Presets/Instruments/Instrument Rack/Missing.adg"],
    )
    ambiguous = browser_module._metadata_for_category_items(
        browser=object(),
        category="user_library_max_for_live",
        offset=0,
        limit=10,
        paths=[r"C:\outside\IMG"],
    )
    combined = path_only["items"] + stale["items"] + ambiguous["items"]

    assert stale["items"][0]["metadata_status"] == "stale_missing_file"
    assert ambiguous["items"][0]["metadata_status"] == "ambiguous_file_match"
    for item in combined:
        browser_path = item["browser_path"]
        assert browser_path
        assert not browser_path.startswith("/")
        assert not browser_path.startswith("//")
        assert ":" not in browser_path
        assert "\\" not in browser_path
        assert browser_path not in absolute_inputs
    assert "outside" not in repr(path_only["items"])
    assert "server" not in repr(path_only["items"])


def test_file_backed_metadata_item_keeps_task1_sanitized_browser_path(
    browser_module, tmp_path, monkeypatch
):
    target = tmp_path / "Warm Pad.adg"
    target.write_bytes(b"payload")
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        browser_module,
        "_resolve_user_library_file",
        lambda _root, _browser_path, _category: target,
    )

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="instrument_racks",
        offset=0,
        limit=10,
        paths=[r"C:\outside\Warm Pad.adg"],
    )

    item = payload["items"][0]
    assert item["metadata_status"] == "file_backed"
    assert item["browser_path"] == "Warm Pad.adg"
    assert str(tmp_path) not in repr(item)
    assert "outside" not in repr(item)


def test_first_oversized_metadata_item_does_not_exceed_page_byte_limit(
    browser_module, tmp_path, monkeypatch
):
    root = tmp_path / "User Library"
    root.mkdir()
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))
    huge_path = "Presets/Instruments/Instrument Rack/%s.adg" % (
        "x" * (browser_module.MAX_METADATA_PAGE_BYTES + 1000)
    )

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="instrument_racks",
        offset=0,
        limit=1,
        paths=[huge_path],
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    assert len(encoded.encode("utf-8")) <= browser_module.MAX_METADATA_PAGE_BYTES
    assert payload["next_offset"] in (None, 1)
    if payload["items"]:
        assert len(payload["items"][0]["browser_path"]) < len(huge_path)


def test_limit_zero_metadata_page_advances_or_finishes(browser_module, tmp_path, monkeypatch):
    root = tmp_path / "User Library"
    root.mkdir()
    monkeypatch.setattr(browser_module, "_bridge_user_library_root", lambda: str(root))

    payload = browser_module._metadata_for_category_items(
        browser=object(),
        category="instrument_racks",
        offset=0,
        limit=0,
        paths=[
            "Presets/Instruments/Instrument Rack/One.adg",
            "Presets/Instruments/Instrument Rack/Two.adg",
        ],
    )

    assert payload["limit"] == 0
    assert payload["next_offset"] is None or payload["next_offset"] > payload["offset"]


def test_metadata_page_endpoint_returns_json_payload_for_category(
    browser_module, monkeypatch
):
    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    handler = browser_module.BrowserHandler.__new__(browser_module.BrowserHandler)
    handler.osc_server = _Server()
    handler._get_browser = lambda: _BrowserStub(user_library=True)
    monkeypatch.setattr(
        browser_module,
        "_metadata_for_category_items",
        lambda browser, category, offset=0, limit=25, paths=None: {
            "category": category,
            "offset": offset,
            "limit": limit,
            "total": 1,
            "next_offset": None,
            "items": [
                {
                    "browser_path": "Presets/Instruments/Instrument Rack/Bass.adg",
                    "name": "Bass.adg",
                    "metadata_status": "path_only",
                    "file_backed_expected": False,
                    "file_exists": False,
                }
            ],
        },
    )

    handler.init_api()
    reply = callbacks["/live/browser/get/metadata_page"](
        ("instrument_racks", 0, 10)
    )
    payload = json.loads(reply[0])

    assert payload["category"] == "instrument_racks"
    assert payload["offset"] == 0
    assert payload["limit"] == 10
    assert payload["total"] == 1
    assert payload["next_offset"] is None
    assert payload["items"][0]["metadata_status"] == "path_only"
