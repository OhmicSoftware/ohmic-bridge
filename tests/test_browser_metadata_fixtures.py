from pathlib import Path

import pytest

from tests.integration import browser_metadata_fixtures as fixtures


def _write(root, relative, payload=b"fixture"):
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_instrument_rack_source_discovery_rejects_drum_rack_paths(
    monkeypatch, tmp_path
):
    root = tmp_path / "User Library"
    _write(root, "Presets/Instruments/Drum Rack/Drum Rack.adg")

    monkeypatch.setattr(fixtures, "user_library_root", lambda: root)

    assert fixtures.discover_source_file(".adg", "instrument_racks") is None


def test_instrument_rack_destination_keeps_category_marker(
    monkeypatch, tmp_path
):
    root = tmp_path / "User Library"
    source = _write(
        root,
        "Presets/Instruments/Instrument Rack/Warm Keys.adg",
    )

    monkeypatch.setattr(fixtures, "user_library_root", lambda: root)

    discovered = fixtures.discover_source_file(".adg", "instrument_racks")
    assert discovered == source
    destination = fixtures.destination_path_for_source(
        root / fixtures.FIXTURE_DIR_NAME,
        source,
        "instrument_racks",
        "Copied.adg",
    )

    assert destination.relative_to(root).as_posix() == (
        "_OhmicMetadataE2E/Presets/Instruments/Instrument Rack/"
        "Ohmic Metadata/Copied.adg"
    )


def test_source_discovery_excludes_remote_scripts_and_owned_fixture(
    monkeypatch, tmp_path
):
    root = tmp_path / "User Library"
    _write(root, "Remote Scripts/Ohmic_Bridge/Bad.adg")
    _write(root, "_OhmicMetadataE2E/Presets/Instruments/Instrument Rack/Bad.adg")
    expected = _write(root, "Presets/Instruments/Instrument Rack/Good.adg")

    monkeypatch.setattr(fixtures, "user_library_root", lambda: root)

    assert fixtures.discover_source_file(".adg", "instrument_racks") == expected


def test_amxd_destination_uses_indexed_max_for_live_category_path(tmp_path):
    root = tmp_path / "User Library"
    source = _write(
        root,
        "Presets/Audio Effects/Max Audio Effect/Imported/Device.amxd",
    )

    destination = fixtures.destination_path_for_source(
        root / fixtures.FIXTURE_DIR_NAME,
        source,
        "user_library_max_for_live",
        "Copied.amxd",
    )

    assert destination.relative_to(root).as_posix() == (
        "_OhmicMetadataE2E/Presets/Audio Effects/Max Audio Effect/"
        "Copied.amxd"
    )


def test_plugin_preset_destination_uses_indexed_plugin_preset_path(tmp_path):
    root = tmp_path / "User Library"
    source = _write(root, "Presets/Instruments/Opus/Violins/Pad.vstpreset")

    destination = fixtures.destination_path_for_source(
        root / fixtures.FIXTURE_DIR_NAME,
        source,
        "plugin_presets",
        "Copied.vstpreset",
    )

    assert destination.relative_to(root).as_posix() == (
        "_OhmicMetadataE2E/Presets/Plug-ins/Ohmic Metadata/Copied.vstpreset"
    )


def test_workspace_adopts_unmarked_test_owned_fixture_root(monkeypatch, tmp_path):
    root = tmp_path / "User Library"
    legacy = _write(
        root,
        "_OhmicMetadataE2E/Presets/Instruments/Instrument Rack/"
        "Ohmic Metadata Test Legacy.adg",
    )
    finalizers = []

    class Request:
        def addfinalizer(self, fn):
            finalizers.append(fn)

    monkeypatch.setattr(fixtures, "user_library_root", lambda: root)

    workspace = fixtures.BrowserMetadataFixtureWorkspace(Request())

    assert workspace.fixture_dir == root / fixtures.FIXTURE_DIR_NAME
    assert workspace.marker_path.is_file()
    assert not legacy.exists()

    for finalizer in finalizers:
        finalizer()

    assert not workspace.fixture_dir.exists()
    assert not (root / fixtures.LOCK_FILE_NAME).exists()


def test_workspace_move_extra_subdir_stays_at_indexed_category_depth(
    monkeypatch, tmp_path
):
    root = tmp_path / "User Library"
    root.mkdir()
    finalizers = []

    class Request:
        def addfinalizer(self, fn):
            finalizers.append(fn)

    monkeypatch.setattr(fixtures, "user_library_root", lambda: root)

    workspace = fixtures.BrowserMetadataFixtureWorkspace(Request())

    moved_dir = workspace.category_dir("instrument_racks", extra_subdir="Moved")
    assert moved_dir.relative_to(root).as_posix() == (
        "_OhmicMetadataE2E/Presets/Instruments/Instrument Rack/Moved"
    )

    for finalizer in finalizers:
        finalizer()


def test_workspace_max_for_live_browser_path_matches_live_display_path(
    monkeypatch, tmp_path
):
    root = tmp_path / "User Library"
    root.mkdir()
    finalizers = []

    class Request:
        def addfinalizer(self, fn):
            finalizers.append(fn)

    monkeypatch.setattr(fixtures, "user_library_root", lambda: root)

    workspace = fixtures.BrowserMetadataFixtureWorkspace(Request())
    path = (
        root
        / fixtures.FIXTURE_DIR_NAME
        / "Presets"
        / "Audio Effects"
        / "Max Audio Effect"
        / "Ohmic Metadata Fixture.amxd"
    )

    assert workspace.expected_browser_path_for(
        path, "user_library_max_for_live"
    ) == "Max Audio Effect/Ohmic Metadata Fixture"

    for finalizer in finalizers:
        finalizer()


def test_workspace_rejects_unmarked_fixture_root_with_non_test_file(
    monkeypatch, tmp_path
):
    root = tmp_path / "User Library"
    personal = _write(
        root,
        "_OhmicMetadataE2E/Presets/Instruments/Instrument Rack/Personal.adg",
    )
    finalizers = []

    class Request:
        def addfinalizer(self, fn):
            finalizers.append(fn)

    monkeypatch.setattr(fixtures, "user_library_root", lambda: root)

    with pytest.raises(AssertionError, match="marker is missing"):
        fixtures.BrowserMetadataFixtureWorkspace(Request())

    for finalizer in finalizers:
        finalizer()

    assert personal.is_file()
    assert not (root / fixtures.LOCK_FILE_NAME).exists()
