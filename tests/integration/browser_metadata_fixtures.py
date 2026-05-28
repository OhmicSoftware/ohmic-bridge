"""Fixture helpers for browser metadata integration tests."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
import time

import pytest


FIXTURE_DIR_NAME = "_OhmicMetadataE2E"
LOCK_FILE_NAME = "_OhmicMetadataE2E.lock"
MARKER_FILE_NAME = ".ohmic_metadata_e2e.json"
REMOTE_SCRIPTS_DIR_NAME = "Remote Scripts"

FILE_TYPE_CASES = (
    (".adg", "instrument_racks"),
    (".adv", "ableton_presets"),
    (".amxd", "user_library_max_for_live"),
    (".vstpreset", "plugin_presets"),
    (".aupreset", "plugin_presets"),
)

_CATEGORY_BY_EXTENSION = dict(FILE_TYPE_CASES)

_FIXED_CATEGORY_SUBDIRS = {
    "instrument_racks": (
        "Presets",
        "Instruments",
        "Instrument Rack",
        "Ohmic Metadata",
    ),
    "ableton_presets": (
        "Presets",
        "Instruments",
        "Ohmic Metadata",
    ),
    "user_library_max_for_live": (
        "Presets",
        "Audio Effects",
        "Max Audio Effect",
    ),
    "plugin_presets": (
        "Presets",
        "Plug-ins",
        "Ohmic Metadata",
    ),
}


@dataclass(frozen=True)
class FixtureFile:
    category: str
    path: Path
    browser_path: str
    source_path: Path


def user_library_root():
    if sys.platform == "win32":
        return Path(r"E:\Ableton\User Library")
    if sys.platform == "darwin":
        return Path("/Users/awilki01/Music/Ableton/User Library")
    pytest.skip(
        "browser metadata integration fixtures only know Adam's Windows "
        "and macOS Ableton User Library roots"
    )


def _require_user_library_root():
    root = user_library_root()
    if not root.is_dir():
        pytest.skip("Ableton User Library root is not present: %s" % root)
    return root


def _normalise_extension(extension):
    extension = str(extension).lower()
    if not extension.startswith("."):
        extension = "." + extension
    if extension not in _CATEGORY_BY_EXTENSION:
        raise AssertionError("unsupported fixture extension %r" % extension)
    return extension


def category_for_extension(extension):
    return _CATEGORY_BY_EXTENSION[_normalise_extension(extension)]


def _fixed_category_subdir(category):
    try:
        return _FIXED_CATEGORY_SUBDIRS[category]
    except KeyError:
        raise AssertionError("unsupported fixture category %r" % category)


def _fixture_category_parts(category, extra_subdir=None):
    parts = list(_fixed_category_subdir(category))
    if extra_subdir:
        if parts and parts[-1] == "Ohmic Metadata":
            parts[-1] = str(extra_subdir)
        else:
            parts.append(str(extra_subdir))
    return parts


def _normalise_relative_path(path):
    return Path(path).as_posix()


def _relative_to_root(root, path):
    return Path(path).resolve().relative_to(Path(root).resolve())


def _user_library_root_for_fixture_dir(fixture_dir):
    fixture_dir = Path(fixture_dir)
    for path in (fixture_dir, *fixture_dir.parents):
        if path.name.lower() == FIXTURE_DIR_NAME.lower():
            return path.parent
    return fixture_dir.parent


def _path_parts(path):
    return [
        part.strip().lower()
        for part in _normalise_relative_path(path).split("/")
        if part.strip()
    ]


def category_for_source_path(root, path):
    relative_path = _relative_to_root(root, path)
    lowered = _normalise_relative_path(relative_path).lower()
    parts = _path_parts(relative_path)

    if lowered.endswith(".adg"):
        if "instrument rack" in parts:
            return "instrument_racks"
        if "drum rack" in parts:
            return "drum_racks"
        if "audio effect rack" in parts:
            return "audio_effect_racks"
        if "midi effect rack" in parts:
            return "midi_effect_racks"
        return None
    if lowered.endswith(".adv"):
        return "ableton_presets"
    if lowered.endswith(".amxd"):
        return "user_library_max_for_live"
    if lowered.endswith((".vstpreset", ".aupreset")):
        return "plugin_presets"
    return None


def source_is_compatible(root, path, category):
    return category_for_source_path(root, path) == category


def _is_excluded_source_dir(name):
    return name.lower() in {
        FIXTURE_DIR_NAME.lower(),
        REMOTE_SCRIPTS_DIR_NAME.lower(),
    }


def _path_contains_excluded_part(path):
    excluded = {FIXTURE_DIR_NAME.lower(), REMOTE_SCRIPTS_DIR_NAME.lower()}
    return any(part.lower() in excluded for part in Path(path).parts)


def discover_source_file(extension, category=None):
    extension = _normalise_extension(extension)
    category = category or category_for_extension(extension)
    root = _require_user_library_root()
    candidates = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames if not _is_excluded_source_dir(name)
        ]
        if _path_contains_excluded_part(dirpath):
            continue
        for filename in filenames:
            if not filename.lower().endswith(extension):
                continue
            path = Path(dirpath) / filename
            if _path_contains_excluded_part(path):
                continue
            try:
                stat_result = path.stat()
            except OSError:
                continue
            if not path.is_file() or stat_result.st_size <= 0:
                continue
            if not source_is_compatible(root, path, category):
                continue
            candidates.append((stat_result.st_size, len(str(path)), path))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def require_source_file(extension, category=None):
    category = category or category_for_extension(extension)
    source = discover_source_file(extension, category)
    if source is None:
        pytest.skip(
            "no %s source file compatible with %s found under Ableton "
            "User Library for browser metadata integration fixture"
            % (_normalise_extension(extension), category)
        )
    return source


def destination_path_for_source(
    fixture_dir, source_path, category, dest_name, extra_subdir=None
):
    fixture_dir = Path(fixture_dir)
    source_path = Path(source_path)
    root = _user_library_root_for_fixture_dir(fixture_dir)
    _relative_to_root(root, source_path)
    parts = _fixture_category_parts(category, extra_subdir=extra_subdir)
    return fixture_dir.joinpath(*parts, dest_name)


def _is_adoptable_unmarked_fixture_dir(fixture_dir):
    fixture_dir = Path(fixture_dir)
    allowed_suffixes = {extension for extension, _category in FILE_TYPE_CASES}
    files = [path for path in fixture_dir.rglob("*") if path.is_file()]
    if not files:
        return True
    for path in files:
        if path.name == MARKER_FILE_NAME:
            continue
        if not path.name.startswith("Ohmic Metadata "):
            return False
        if path.suffix.lower() not in allowed_suffixes:
            return False
    return True


class BrowserMetadataFixtureWorkspace:
    def __init__(self, request):
        if os.environ.get("PYTEST_XDIST_WORKER"):
            pytest.skip(
                "browser metadata integration tests mutate one shared "
                "Ableton User Library fixture; do not run with pytest-xdist"
            )
        self.root = _require_user_library_root()
        self.fixture_dir = self.root / FIXTURE_DIR_NAME
        self.lock_path = self.root / LOCK_FILE_NAME
        self.marker_path = self.fixture_dir / MARKER_FILE_NAME
        self._lock_fd = None
        self._setup_cleanup_failed = False

        self._acquire_lock()
        request.addfinalizer(self._finalize)
        try:
            self.cleanup_filesystem()
        except BaseException:
            self._setup_cleanup_failed = True
            raise
        self.fixture_dir.mkdir(parents=True, exist_ok=True)
        self.marker_path.write_text(
            json.dumps(
                {
                    "owner": "Ohmic browser metadata integration tests",
                    "pid": os.getpid(),
                    "created_at": time.time(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _acquire_lock(self):
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            self._lock_fd = os.open(os.fspath(self.lock_path), flags)
        except FileExistsError:
            pytest.fail(
                "Refusing to touch %s because lock file already exists: %s"
                % (self.fixture_dir, self.lock_path)
            )
        os.write(self._lock_fd, str(os.getpid()).encode("ascii"))

    def _release_lock(self):
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass

    def _finalize(self):
        try:
            if not self._setup_cleanup_failed:
                self.cleanup_filesystem()
        finally:
            self._release_lock()

    def cleanup_filesystem(self):
        if not self.fixture_dir.exists():
            return
        if not self.fixture_dir.is_dir():
            raise AssertionError(
                "Refusing cleanup because fixture path is not a directory: %s"
                % self.fixture_dir
            )
        if not self.marker_path.is_file():
            if not _is_adoptable_unmarked_fixture_dir(self.fixture_dir):
                raise AssertionError(
                    "Refusing to delete %s because marker is missing: %s"
                    % (self.fixture_dir, self.marker_path)
                )
            self.marker_path.write_text(
                json.dumps(
                    {
                        "owner": "Ohmic browser metadata integration tests",
                        "adopted_unmarked_fixture": True,
                        "pid": os.getpid(),
                        "created_at": time.time(),
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        shutil.rmtree(self.fixture_dir)

    def category_dir(self, category, extra_subdir=None):
        parts = _fixture_category_parts(category, extra_subdir=extra_subdir)
        return self.fixture_dir.joinpath(*parts)

    def browser_path_for(self, path):
        return path.relative_to(self.root).as_posix()

    def expected_browser_path_for(self, path, category):
        if category == "user_library_max_for_live":
            parts = list(Path(path).parts)
            for marker in ("Max Audio Effect", "Max MIDI Effect", "Max Instrument"):
                if marker in parts:
                    index = parts.index(marker)
                    return "/".join(parts[index:-1] + [Path(path).stem])
        return self.browser_path_for(path)

    def _fixture_file(self, path, source_path, category):
        return FixtureFile(
            category=category,
            path=path,
            browser_path=self.expected_browser_path_for(path, category),
            source_path=source_path,
        )

    def copy_fixture(self, extension, dest_name=None, extra_subdir=None):
        extension = _normalise_extension(extension)
        category = category_for_extension(extension)
        source = require_source_file(extension, category)
        name = dest_name or ("Ohmic Metadata Fixture%s" % extension)
        if not name.lower().endswith(extension):
            name += extension
        dest = destination_path_for_source(
            self.fixture_dir,
            source,
            category,
            name,
            extra_subdir=extra_subdir,
        )
        dest_dir = dest.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return self._fixture_file(dest, source, category)

    def rename_fixture(self, fixture_file, dest_name):
        dest = fixture_file.path.with_name(dest_name)
        fixture_file.path.rename(dest)
        return self._fixture_file(dest, fixture_file.source_path, fixture_file.category)

    def move_fixture(self, fixture_file, extra_subdir, dest_name=None):
        dest_dir = self.category_dir(
            fixture_file.category, extra_subdir=extra_subdir
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (dest_name or fixture_file.path.name)
        fixture_file.path.rename(dest)
        return self._fixture_file(dest, fixture_file.source_path, fixture_file.category)

    def move_fixture_to_path(self, fixture_file, target_path):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_file.path.rename(target_path)
        return self._fixture_file(
            target_path, fixture_file.source_path, fixture_file.category
        )

    def copy_existing_fixture(self, fixture_file, dest_name):
        dest = fixture_file.path.with_name(dest_name)
        shutil.copy2(fixture_file.path, dest)
        return self._fixture_file(dest, fixture_file.source_path, fixture_file.category)

    def cleanup_and_verify_browser_invisible(
        self, osc, fixture_files, timeout=30.0
    ):
        self.cleanup_filesystem()
        assert not self.fixture_dir.exists(), (
            "fixture folder still exists after cleanup: %s" % self.fixture_dir
        )
        for fixture_file in fixture_files:
            _wait_browser_path_absent(
                osc, fixture_file.category, fixture_file.browser_path, timeout
            )


def _metadata_page(osc, category, offset=0, limit=25):
    reply = osc.query(
        "/live/browser/get/metadata_page", [category, offset, limit], timeout=5.0
    )
    if len(reply) != 1:
        raise AssertionError(
            "metadata_page should return one JSON payload, got %r" % (reply,)
        )
    payload = json.loads(str(reply[0]))
    if "error" in payload:
        raise AssertionError(
            "metadata_page returned error for %r: %r" % (category, payload)
        )
    return payload


def _metadata_pages(osc, category):
    pages = []
    offset = 0
    seen_offsets = set()
    while offset is not None:
        if offset in seen_offsets:
            raise AssertionError(
                "metadata_page repeated offset %r for %r; pages=%r"
                % (offset, category, pages)
            )
        seen_offsets.add(offset)
        payload = _metadata_page(osc, category, offset=offset)
        pages.append(payload)
        offset = payload.get("next_offset")
    return pages


def _active_paths(pages):
    paths = set()
    for payload in pages:
        for item in payload.get("items", ()):
            if item.get("metadata_status") != "stale_missing_file":
                paths.add(item.get("browser_path"))
    return paths


def _wait_browser_path_absent(osc, category, browser_path, timeout):
    deadline = time.monotonic() + timeout
    last_pages = []
    while time.monotonic() < deadline:
        last_pages = _metadata_pages(osc, category)
        if browser_path not in _active_paths(last_pages):
            return
        time.sleep(0.25)
    raise AssertionError(
        "fixture browser path remained active after cleanup: %r diagnostics=%r"
        % (browser_path, last_pages)
    )
