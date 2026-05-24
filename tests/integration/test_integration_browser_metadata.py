"""Integration tests for browser metadata file-backed identity.

do not parallelize - these tests mutate an owned folder in the Ableton
User Library and depend on Live's single shared browser index. Running
them concurrently can race filesystem setup, cleanup, and browser refresh.
"""
import hashlib
import json
import time

import pytest

from tests.integration.browser_metadata_fixtures import (
    FILE_TYPE_CASES,
    BrowserMetadataFixtureWorkspace,
)


pytestmark = pytest.mark.integration

METADATA_ENDPOINT = "/live/browser/get/metadata_page"


def _metadata_page(osc, category, offset=0, limit=25):
    reply = osc.query(METADATA_ENDPOINT, [category, offset, limit], timeout=5.0)
    assert len(reply) == 1, (
        "metadata_page should return one JSON payload, got %r" % (reply,)
    )
    payload = json.loads(str(reply[0]))
    assert "error" not in payload, (
        "metadata_page returned error for %r: %r" % (category, payload)
    )
    return payload


def _metadata_pages(osc, category):
    pages = []
    offset = 0
    seen_offsets = set()
    while offset is not None:
        assert offset not in seen_offsets, (
            "metadata_page repeated offset %r for %r; pages=%r"
            % (offset, category, pages)
        )
        seen_offsets.add(offset)
        payload = _metadata_page(osc, category, offset=offset)
        pages.append(payload)
        offset = payload.get("next_offset")
    return pages


def _iter_items(pages):
    for payload in pages:
        for item in payload.get("items", ()):
            yield item


def _diagnose_path(pages, browser_path):
    matches = [
        item
        for item in _iter_items(pages)
        if item.get("browser_path") == browser_path
    ]
    stale = [
        item
        for item in matches
        if item.get("metadata_status") == "stale_missing_file"
    ]
    return {
        "path": browser_path,
        "matches": matches,
        "stale_missing_file": stale,
        "page_count": len(pages),
        "totals": [payload.get("total") for payload in pages],
    }


def wait_for_metadata_path(
    osc, category, browser_path, present=True, timeout=30.0
):
    deadline = time.monotonic() + timeout
    last_pages = []
    while time.monotonic() < deadline:
        last_pages = _metadata_pages(osc, category)
        active = {
            item.get("browser_path"): item
            for item in _iter_items(last_pages)
            if item.get("metadata_status") != "stale_missing_file"
        }
        if present and browser_path in active:
            return active[browser_path]
        if not present and browser_path not in active:
            return None
        time.sleep(0.25)

    expected = "appear" if present else "disappear"
    pytest.fail(
        "Timed out waiting for %s to %s in %s. Diagnostics: %r"
        % (
            browser_path,
            expected,
            category,
            _diagnose_path(last_pages, browser_path),
        )
    )


def wait_for_metadata_identity(
    osc, category, browser_path, expected, timeout=30.0
):
    deadline = time.monotonic() + timeout
    last_item = None
    while time.monotonic() < deadline:
        item = wait_for_metadata_path(
            osc, category, browser_path, present=True, timeout=5.0
        )
        last_item = item
        if all(item.get(key) == value for key, value in expected.items()):
            return item
        time.sleep(0.25)
    pytest.fail(
        "Timed out waiting for metadata identity at %r. expected=%r last=%r"
        % (browser_path, expected, last_item)
    )


def _assert_common_file_backed_metadata(item, fixture_file):
    assert item["browser_path"] == fixture_file.browser_path
    assert item["name"] == fixture_file.browser_path.rsplit("/", 1)[-1]
    assert item["extension"] == fixture_file.path.suffix.lower()
    assert item["size"] == fixture_file.path.stat().st_size
    assert item["mtime_ns"] == fixture_file.path.stat().st_mtime_ns
    if item.get("sha256_status") == "ready":
        assert item["sha256"] == _sha256_for_path(fixture_file.path)
    assert item["metadata_status"] in ("file_backed", "hash_only")


def _sha256_for_path(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_user_library_adg_metadata_identity_survives_lifecycle(osc, request):
    workspace = BrowserMetadataFixtureWorkspace(request)
    original = workspace.copy_fixture(
        ".adg", dest_name="Ohmic Metadata Lifecycle.adg"
    )

    first = wait_for_metadata_path(
        osc, original.category, original.browser_path, present=True
    )
    _assert_common_file_backed_metadata(first, original)
    assert first["file_id"], "Lifecycle .adg must expose a file_id: %r" % first
    original_file_id = first["file_id"]
    original_sha256 = first["sha256"]
    original_size = first["size"]

    renamed = workspace.rename_fixture(
        original, "Ohmic Metadata Lifecycle Renamed.adg"
    )
    wait_for_metadata_path(
        osc, original.category, original.browser_path, present=False
    )
    renamed_item = wait_for_metadata_identity(
        osc,
        renamed.category,
        renamed.browser_path,
        {"file_id": original_file_id},
    )
    assert renamed_item["sha256"] == original_sha256
    assert renamed_item["size"] == original_size

    moved = workspace.move_fixture(
        renamed, "Moved", "Ohmic Metadata Lifecycle Renamed.adg"
    )
    wait_for_metadata_path(
        osc, renamed.category, renamed.browser_path, present=False
    )
    moved_item = wait_for_metadata_identity(
        osc,
        moved.category,
        moved.browser_path,
        {"file_id": original_file_id},
    )
    assert moved_item["sha256"] == original_sha256
    assert moved_item["size"] == original_size

    copied = workspace.copy_existing_fixture(
        moved, "Ohmic Metadata Lifecycle Copy.adg"
    )
    copied_item = wait_for_metadata_path(
        osc, copied.category, copied.browser_path, present=True
    )
    assert copied_item["sha256"] == moved_item["sha256"]
    assert copied_item["size"] == moved_item["size"]
    assert copied_item["file_id"] != moved_item["file_id"]

    moved.path.unlink()
    wait_for_metadata_path(osc, moved.category, moved.browser_path, present=False)

    restored = workspace.move_fixture_to_path(copied, original.path)
    assert restored.browser_path == original.browser_path
    wait_for_metadata_path(osc, copied.category, copied.browser_path, present=False)
    wait_for_metadata_path(
        osc, restored.category, restored.browser_path, present=True
    )

    workspace.cleanup_and_verify_browser_invisible(
        osc, [original, renamed, moved, copied, restored]
    )


@pytest.mark.parametrize(("extension", "category"), FILE_TYPE_CASES)
def test_user_library_metadata_file_type_matrix(osc, request, extension, category):
    workspace = BrowserMetadataFixtureWorkspace(request)
    fixture_file = workspace.copy_fixture(extension)

    item = wait_for_metadata_path(
        osc, fixture_file.category, fixture_file.browser_path, present=True
    )
    assert fixture_file.category == category
    assert item["browser_path"] == fixture_file.browser_path
    assert item["name"] == fixture_file.browser_path.rsplit("/", 1)[-1]
    assert item["metadata_status"] != "stale_missing_file"

    if extension == ".amxd" and item["metadata_status"] != "file_backed":
        assert not item.get("file_backed_expected"), (
            "Bridge must not claim ambiguous/unmapped .amxd is file_backed: %r"
            % (item,)
        )
        pytest.skip(
            ".amxd file-backed identity skipped because Live browser path "
            "did not resolve unambiguously to one User Library .amxd: %r"
            % (item,)
        )

    for field in (
        "extension",
        "size",
        "mtime_ns",
        "sha256",
        "metadata_status",
    ):
        assert field in item, "%s missing from metadata item %r" % (field, item)
    _assert_common_file_backed_metadata(item, fixture_file)

    workspace.cleanup_and_verify_browser_invisible(osc, [fixture_file])
