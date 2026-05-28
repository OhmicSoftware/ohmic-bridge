import hashlib
import importlib
import logging
from pathlib import Path
import shutil
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "Ohmic_Bridge"


@pytest.fixture
def browser_metadata_module():
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == PACKAGE_NAME or name.startswith(f"{PACKAGE_NAME}.")
    }

    bridge_package = types.ModuleType(PACKAGE_NAME)
    bridge_package.__path__ = [str(ROOT)]
    abletonosc_package = types.ModuleType(f"{PACKAGE_NAME}.abletonosc")
    abletonosc_package.__path__ = [str(ROOT / "abletonosc")]
    sys.modules[PACKAGE_NAME] = bridge_package
    sys.modules[f"{PACKAGE_NAME}.abletonosc"] = abletonosc_package

    try:
        module = importlib.import_module(f"{PACKAGE_NAME}.abletonosc.browser_metadata")
        module._SHA256_CACHE.clear()
        yield module
    finally:
        for name in tuple(sys.modules):
            if name == PACKAGE_NAME or name.startswith(f"{PACKAGE_NAME}."):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_metadata_for_path_includes_hash_size_mtime_and_darwin_file_id(
    browser_metadata_module, monkeypatch, tmp_path
):
    browser_metadata = browser_metadata_module
    payload = b"ableton test payload"
    path = tmp_path / "Warm Pad.adg"
    path.write_bytes(payload)

    monkeypatch.setattr(browser_metadata.sys, "platform", "darwin")
    monkeypatch.setattr(
        browser_metadata.os,
        "stat",
        lambda _path: type(
            "S",
            (),
            {
                "st_dev": 7,
                "st_ino": 99,
                "st_size": len(payload),
                "st_mtime": 1700000000.25,
                "st_mtime_ns": 1700000000250000000,
            },
        )(),
    )

    data = browser_metadata.metadata_for_file(
        path,
        "instrument_racks",
        "Presets/Instruments/Instrument Rack/Warm Pad.adg",
    )

    assert data["browser_path"] == "Presets/Instruments/Instrument Rack/Warm Pad.adg"
    assert data["name"] == "Warm Pad.adg"
    assert data["extension"] == ".adg"
    assert data["category"] == "instrument_racks"
    assert data["file_id"] == "stat:darwin:7:99"
    assert data["size"] == len(payload)
    assert data["mtime_ns"] == 1700000000250000000
    assert data["sha256"] == hashlib.sha256(payload).hexdigest()
    assert data["sha256_status"] == "ready"
    assert str(tmp_path) not in repr(data)


def test_file_id_for_path_uses_windows_stat_identity(
    browser_metadata_module, monkeypatch, tmp_path
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Preset.vstpreset"
    path.write_bytes(b"payload")

    monkeypatch.setattr(browser_metadata.sys, "platform", "win32")

    real_stat = browser_metadata.os.stat
    real_path_stat = real_stat(path)

    def fake_stat(target, *args, **kwargs):
        if Path(target) == path:
            return type(
                "S",
                (),
                {
                    "st_dev": 123,
                    "st_ino": 456,
                    "st_size": real_path_stat.st_size,
                    "st_mtime": real_path_stat.st_mtime,
                    "st_mtime_ns": real_path_stat.st_mtime_ns,
                    "st_mode": real_path_stat.st_mode,
                },
            )()
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(
        browser_metadata.os,
        "stat",
        fake_stat,
    )
    monkeypatch.setattr(
        browser_metadata,
        "_win32_volume_file_index",
        lambda _path: pytest.fail("ctypes fallback should not run when stat has identity"),
    )

    assert browser_metadata.file_id_for_path(path) == "stat:win32:123:456"


def test_file_id_for_path_uses_windows_native_fallback_when_stat_has_no_identity(
    browser_metadata_module, monkeypatch, tmp_path
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Preset.vstpreset"
    path.write_bytes(b"payload")

    monkeypatch.setattr(browser_metadata.sys, "platform", "win32")

    real_stat = browser_metadata.os.stat
    real_path_stat = real_stat(path)

    def fake_stat(target, *args, **kwargs):
        if Path(target) == path:
            return type(
                "S",
                (),
                {
                    "st_dev": 0,
                    "st_ino": 0,
                    "st_size": real_path_stat.st_size,
                    "st_mtime": real_path_stat.st_mtime,
                    "st_mtime_ns": real_path_stat.st_mtime_ns,
                    "st_mode": real_path_stat.st_mode,
                },
            )()
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(
        browser_metadata.os,
        "stat",
        fake_stat,
    )
    monkeypatch.setattr(
        browser_metadata,
        "_win32_volume_file_index",
        lambda _path: ("a1b2c3d4", "0000000000000005-0000000000000011"),
    )

    assert (
        browser_metadata.file_id_for_path(path)
        == "fileid:win32:a1b2c3d4:0000000000000005-0000000000000011"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file identity API")
def test_windows_file_id_survives_rename_and_same_volume_move(
    browser_metadata_module, tmp_path
):
    browser_metadata = browser_metadata_module
    original = tmp_path / "Original.adg"
    renamed = tmp_path / "Renamed.adg"
    moved_dir = tmp_path / "Moved"
    moved_dir.mkdir()
    moved = moved_dir / "Renamed.adg"
    copied = tmp_path / "Copy.adg"

    original.write_bytes(b"payload")
    original_id = browser_metadata.file_id_for_path(original)
    assert original_id is not None

    original.rename(renamed)
    assert browser_metadata.file_id_for_path(renamed) == original_id

    renamed.rename(moved)
    assert browser_metadata.file_id_for_path(moved) == original_id

    shutil.copy2(moved, copied)
    assert browser_metadata.file_id_for_path(copied) != original_id


def test_large_cold_cache_file_returns_pending_hash_status(
    browser_metadata_module, tmp_path
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Too Big.adg"
    path.write_bytes(b"x" * (browser_metadata.MAX_SINGLE_SYNC_HASH_BYTES + 1))

    data = browser_metadata.metadata_for_file(
        path,
        "instrument_racks",
        "Presets/Instruments/Instrument Rack/Too Big.adg",
    )

    assert data["sha256"] is None
    assert data["sha256_status"] == "pending"


def test_cached_small_file_returns_ready_and_reuses_unchanged_hash(
    browser_metadata_module, monkeypatch, tmp_path
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Small.adv"
    path.write_bytes(b"small payload")
    calls = []

    def fake_sha256_for_path(_path):
        calls.append(Path(_path))
        return "abc123"

    monkeypatch.setattr(browser_metadata, "sha256_for_path", fake_sha256_for_path)

    first = browser_metadata.metadata_for_file(
        path,
        "ableton_presets",
        "Presets/Instruments/Operator/Small.adv",
    )
    second = browser_metadata.metadata_for_file(
        path,
        "ableton_presets",
        "Presets/Instruments/Operator/Small.adv",
    )

    assert first["sha256"] == "abc123"
    assert first["sha256_status"] == "ready"
    assert second["sha256"] == "abc123"
    assert second["sha256_status"] == "ready"
    assert calls == [path]


def test_hash_budget_marks_supported_file_pending_when_remaining_budget_too_small(
    browser_metadata_module, tmp_path
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Budgeted.amxd"
    path.write_bytes(b"payload")

    data = browser_metadata.metadata_for_file(
        path,
        "user_library_max_for_live",
        "Presets/Max for Live/Max Audio Effect/Budgeted.amxd",
        hash_budget={"remaining": 1},
    )

    assert data["sha256"] is None
    assert data["sha256_status"] == "pending"


def test_shared_remaining_bytes_budget_depletion_marks_second_file_pending(
    browser_metadata_module, tmp_path
):
    browser_metadata = browser_metadata_module
    first_path = tmp_path / "First.adg"
    second_path = tmp_path / "Second.adg"
    first_path.write_bytes(b"first payload")
    second_path.write_bytes(b"second payload")
    budget = {"remaining_bytes": first_path.stat().st_size}

    first = browser_metadata.metadata_for_file(
        first_path,
        "instrument_racks",
        "Presets/Instruments/Instrument Rack/First.adg",
        hash_budget=budget,
    )
    second = browser_metadata.metadata_for_file(
        second_path,
        "instrument_racks",
        "Presets/Instruments/Instrument Rack/Second.adg",
        hash_budget=budget,
    )

    assert first["sha256_status"] == "ready"
    assert first["sha256"]
    assert second["sha256"] is None
    assert second["sha256_status"] == "pending"
    assert budget["remaining"] == 0
    assert budget["remaining_bytes"] == 0


def test_unsupported_extension_returns_none(browser_metadata_module, tmp_path):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Audio.wav"
    path.write_bytes(b"payload")

    assert (
        browser_metadata.metadata_for_file(
            path,
            "samples",
            "Samples/Audio.wav",
        )
        is None
    )


def test_unsafe_sha256_for_stat_key_helper_is_not_exported(browser_metadata_module):
    assert not hasattr(browser_metadata_module, "sha256_for_stat_key")


def test_metadata_returns_none_and_logs_warning_when_stat_fails(
    browser_metadata_module, monkeypatch, tmp_path, caplog
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Missing.adg"
    real_stat = browser_metadata.os.stat

    def fail_stat(target, *args, **kwargs):
        if Path(target) != path:
            return real_stat(target, *args, **kwargs)
        raise OSError("stat denied")

    monkeypatch.setattr(browser_metadata.os, "stat", fail_stat)

    with caplog.at_level(logging.WARNING, logger="abletonosc"):
        data = browser_metadata.metadata_for_file(
            path,
            "instrument_racks",
            "Presets/Instruments/Instrument Rack/Missing.adg",
        )

    assert data is None
    assert "Failed to stat browser metadata file" in caplog.text


def test_hash_read_failure_returns_pending_metadata_not_exception(
    browser_metadata_module, monkeypatch, tmp_path, caplog
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Unreadable.adg"
    path.write_bytes(b"payload")

    def fail_hash(_path):
        raise OSError("read denied")

    monkeypatch.setattr(browser_metadata, "sha256_for_path", fail_hash)

    with caplog.at_level(logging.WARNING, logger="abletonosc"):
        data = browser_metadata.metadata_for_file(
            path,
            "instrument_racks",
            "Presets/Instruments/Instrument Rack/Unreadable.adg",
        )

    assert data["sha256"] is None
    assert data["sha256_status"] == "pending"
    assert "Failed to hash browser metadata file" in caplog.text


def test_file_id_failure_returns_metadata_with_none_file_id(
    browser_metadata_module, monkeypatch, tmp_path, caplog
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "No File Id.adg"
    path.write_bytes(b"payload")

    def fail_file_id(_path):
        raise OSError("identity denied")

    monkeypatch.setattr(browser_metadata, "file_id_for_path", fail_file_id)

    with caplog.at_level(logging.WARNING, logger="abletonosc"):
        data = browser_metadata.metadata_for_file(
            path,
            "instrument_racks",
            "Presets/Instruments/Instrument Rack/No File Id.adg",
        )

    assert data["file_id"] is None
    assert data["sha256"] == hashlib.sha256(b"payload").hexdigest()
    assert data["sha256_status"] == "ready"
    assert "Failed to collect browser metadata file identity" in caplog.text


def test_file_changed_during_hash_returns_pending_and_does_not_cache(
    browser_metadata_module, monkeypatch, tmp_path
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Race.adg"
    path.write_bytes(b"payload")
    real_stat = browser_metadata.os.stat
    first_stat = real_stat(path)
    changed_stat = type(
        "S",
        (),
        {
            "st_size": first_stat.st_size + 1,
            "st_mtime_ns": first_stat.st_mtime_ns + 1,
        },
    )()
    stats = [first_stat, changed_stat]

    def fake_stat(target, *args, **kwargs):
        if Path(target) != path:
            return real_stat(target, *args, **kwargs)
        if len(stats) > 1:
            return stats.pop(0)
        return stats[0]

    monkeypatch.setattr(browser_metadata.os, "stat", fake_stat)
    monkeypatch.setattr(browser_metadata, "file_id_for_path", lambda _path: "fileid:1")
    monkeypatch.setattr(browser_metadata, "sha256_for_path", lambda _path: "digest")

    data = browser_metadata.metadata_for_file(
        path,
        "instrument_racks",
        "Presets/Instruments/Instrument Rack/Race.adg",
    )

    assert data["sha256"] is None
    assert data["sha256_status"] == "pending"
    assert browser_metadata._SHA256_CACHE == {}


def test_cached_hash_verifies_current_identity_before_returning_ready(
    browser_metadata_module, monkeypatch, tmp_path
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "Cached Race.adg"
    path.write_bytes(b"payload")
    real_stat = browser_metadata.os.stat
    first_stat = real_stat(path)
    changed_stat = type(
        "S",
        (),
        {
            "st_size": first_stat.st_size + 1,
            "st_mtime_ns": first_stat.st_mtime_ns + 1,
        },
    )()
    cache_key = browser_metadata._sha256_cache_key(path, first_stat, "fileid:old")
    browser_metadata._SHA256_CACHE[cache_key] = "cached-digest"
    stats = [first_stat, changed_stat]
    file_ids = ["fileid:old", "fileid:new"]

    def fake_stat(target, *args, **kwargs):
        if Path(target) != path:
            return real_stat(target, *args, **kwargs)
        if len(stats) > 1:
            return stats.pop(0)
        return stats[0]

    def fake_file_id(_path):
        if len(file_ids) > 1:
            return file_ids.pop(0)
        return file_ids[0]

    monkeypatch.setattr(browser_metadata.os, "stat", fake_stat)
    monkeypatch.setattr(browser_metadata, "file_id_for_path", fake_file_id)
    monkeypatch.setattr(
        browser_metadata,
        "sha256_for_path",
        lambda _path: pytest.fail("cached hit should not rehash"),
    )

    data = browser_metadata.metadata_for_file(
        path,
        "instrument_racks",
        "Presets/Instruments/Instrument Rack/Cached Race.adg",
    )

    assert data["sha256"] is None
    assert data["sha256_status"] == "pending"


def test_absolute_drive_or_unc_browser_path_returns_only_leaf(
    browser_metadata_module, tmp_path
):
    browser_metadata = browser_metadata_module
    path = tmp_path / "No Leak.adg"
    path.write_bytes(b"payload")
    unsafe_browser_paths = [
        str(tmp_path / "No Leak.adg"),
        r"C:\Users\Adam\No Leak.adg",
        r"\\server\share\No Leak.adg",
    ]

    for browser_path in unsafe_browser_paths:
        data = browser_metadata.metadata_for_file(
            path,
            "instrument_racks",
            browser_path,
        )

        assert data["browser_path"] == "No Leak.adg"
        assert "Users" not in data["browser_path"]
        assert "server" not in data["browser_path"]
