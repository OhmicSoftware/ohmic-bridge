import Live
import json as _json
import logging
import os
from pathlib import Path
from pathlib import PureWindowsPath
import sys
from typing import Tuple, Optional
from . import browser_metadata
from .handler import AbletonOSCHandler, guarded_lom, guarded_lom_json

logger = logging.getLogger("abletonosc")

# Maps category strings to browser property names.
# Only categories where hasattr(browser, name) is True will be reported as supported.
CATEGORY_MAP = {
    "instruments": "instruments",
    "audio_effects": "audio_effects",
    "midi_effects": "midi_effects",
    "plugins": "plugins",
    "instrument_racks": "user_library",
    "drum_racks": "user_library",
    "audio_effect_racks": "user_library",
    "midi_effect_racks": "user_library",
    "ableton_presets": "user_library",
    "plugin_presets": "user_library",
    "user_library_max_for_live": "max_for_live",
    "max_for_live": "max_for_live",
}

USER_LIBRARY_CATEGORIES = {
    "instrument_racks",
    "drum_racks",
    "audio_effect_racks",
    "midi_effect_racks",
    "ableton_presets",
    "plugin_presets",
    "user_library_max_for_live",
}

PRESET_CATEGORIES = {"ableton_presets", "plugin_presets"}
MAX_FOR_LIVE_UNSUPPORTED_ERROR = "error: Max for Live is not supported by this Ableton Live edition/session"


def _is_preset_category(category):
    return category in PRESET_CATEGORIES


def _device_names(track):
    try:
        return tuple(str(device.name) for device in track.devices)
    except Exception:
        return ()


def _find_inserted_device_index(before, after):
    if len(after) <= len(before):
        return -1
    for index in range(len(after)):
        candidate = after[:index] + after[index + 1:]
        if candidate == before:
            return index
    return -1


def _max_for_live_supported(browser):
    return hasattr(browser, "max_for_live")


def _max_for_live_unsupported_error(category, browser):
    if category == "max_for_live" and not _max_for_live_supported(browser):
        return (MAX_FOR_LIVE_UNSUPPORTED_ERROR,)
    return None


MAX_DEPTH = 5
MAX_RESULTS = 500
MAX_METADATA_PAGE_LIMIT = 25
MAX_METADATA_PAGE_BYTES = 45000
MAX_METADATA_BROWSER_PATH_CHARS = 1024
MAX_FOR_LIVE_COMPOSITE_ATTRS = (
    "max_for_live",
    "audio_effects",
    "midi_effects",
    "instruments",
)
_USER_LIBRARY_MAX_FOR_LIVE_STEMS = None
_INSTALLED_MAX_FOR_LIVE_STEMS = None


def _get_children(item):
    """Get child items from a BrowserItem or iterable.

    browser.instruments etc. return a BrowserItemVector (iterable of BrowserItem).
    BrowserItem.children also returns a BrowserItemVector.
    This helper handles both by trying iteration first, then .children.
    """
    # If it's directly iterable (BrowserItemVector), return as list
    try:
        items = list(item)
        if items:
            return items
    except TypeError:
        pass
    # Fall back to .children for a single BrowserItem
    try:
        return list(item.children)
    except Exception:
        pass
    return []


def _normalise_browser_path(path):
    return str(path).replace("\\", "/").strip()


def _safe_browser_relative_path(path):
    text = str(path).strip()
    normalised = _normalise_browser_path(text)
    if not normalised:
        return None
    if normalised.startswith("/") or normalised.startswith("//"):
        return None
    windows_path = PureWindowsPath(text)
    if windows_path.is_absolute() or windows_path.drive:
        return None
    parts = [part for part in normalised.split("/") if part]
    if any(part in (".", "..") for part in parts):
        return None
    return "/".join(parts)


def _safe_output_browser_path(path):
    text = _normalise_browser_path(path)
    relative_path = _safe_browser_relative_path(text)
    if relative_path:
        safe_path = relative_path
    else:
        windows_leaf = PureWindowsPath(str(path)).name
        safe_path = windows_leaf or text.strip("/").rsplit("/", 1)[-1].strip()
    if not safe_path:
        safe_path = "unknown"
    if len(safe_path) > MAX_METADATA_BROWSER_PATH_CHARS:
        leaf = safe_path.rsplit("/", 1)[-1]
        safe_path = leaf if len(leaf) <= MAX_METADATA_BROWSER_PATH_CHARS else leaf[:MAX_METADATA_BROWSER_PATH_CHARS]
    return safe_path


def _canonical_inside_root(root, candidate):
    try:
        root_resolved = Path(root).resolve(strict=True)
        candidate_resolved = Path(candidate).resolve(strict=True)
    except OSError:
        return None

    try:
        common = os.path.commonpath([
            os.path.normcase(os.fspath(root_resolved)),
            os.path.normcase(os.fspath(candidate_resolved)),
        ])
    except (OSError, ValueError):
        return None
    if common != os.path.normcase(os.fspath(root_resolved)):
        return None
    return candidate_resolved


def _relative_to_root(root, candidate):
    try:
        return _normalise_browser_path(Path(candidate).relative_to(Path(root)))
    except ValueError:
        return ""


def _path_parts(path):
    return [
        part.strip().lower()
        for part in _normalise_browser_path(path).split("/")
        if part.strip()
    ]


def _max_for_live_stem(path):
    leaf = _normalise_browser_path(path).rsplit("/", 1)[-1].strip()
    if leaf.lower().endswith(".amxd"):
        leaf = leaf[:-5]
    return leaf.strip().lower()


def _normalised_stem_set(stems):
    if not stems:
        return set()
    return {_max_for_live_stem(stem) for stem in stems if str(stem).strip()}


def _path_matches_stem_set(path, stems):
    return _max_for_live_stem(path) in _normalised_stem_set(stems)


def _bridge_user_library_root():
    current = os.path.abspath(os.path.dirname(__file__))
    while True:
        if os.path.basename(current).lower() == "remote scripts":
            return os.path.dirname(current)
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _amxd_stems_under(root):
    stems = set()
    if not root or not os.path.isdir(root):
        return stems
    try:
        walker = os.walk(root)
        for _dirpath, _dirnames, filenames in walker:
            for filename in filenames:
                if filename.startswith("._"):
                    continue
                if filename.lower().endswith(".amxd"):
                    stems.add(filename[:-5])
    except Exception as exc:
        logger.warning("Failed to scan Max for Live devices under %s: %s", root, exc)
    return stems


def _get_user_library_max_for_live_stems():
    global _USER_LIBRARY_MAX_FOR_LIVE_STEMS
    if _USER_LIBRARY_MAX_FOR_LIVE_STEMS is not None:
        return _USER_LIBRARY_MAX_FOR_LIVE_STEMS
    _USER_LIBRARY_MAX_FOR_LIVE_STEMS = _amxd_stems_under(
        _bridge_user_library_root()
    )
    return _USER_LIBRARY_MAX_FOR_LIVE_STEMS


def _installed_max_for_live_roots():
    roots = []

    resources = os.path.abspath(
        os.path.join(os.path.dirname(sys.executable), os.pardir, "Resources")
    )
    if os.path.isdir(resources):
        roots.append(resources)

    user_library = _bridge_user_library_root()
    if user_library:
        ableton_home = os.path.dirname(user_library)
        for folder_name in ("Factory Packs", "Packs"):
            candidate = os.path.join(ableton_home, folder_name)
            if os.path.isdir(candidate):
                roots.append(candidate)

    deduped = []
    seen = set()
    for root in roots:
        norm = os.path.normcase(os.path.abspath(root))
        if norm not in seen:
            seen.add(norm)
            deduped.append(root)
    return deduped


def _get_installed_max_for_live_stems():
    global _INSTALLED_MAX_FOR_LIVE_STEMS
    if _INSTALLED_MAX_FOR_LIVE_STEMS is not None:
        return _INSTALLED_MAX_FOR_LIVE_STEMS
    stems = set()
    for root in _installed_max_for_live_roots():
        stems.update(_amxd_stems_under(root))
    _INSTALLED_MAX_FOR_LIVE_STEMS = stems
    return _INSTALLED_MAX_FOR_LIVE_STEMS


def _category_for_user_library_path(path):
    text = _normalise_browser_path(path)
    lowered = text.lower()
    parts = _path_parts(text)

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

    if lowered.endswith((".vstpreset", ".aupreset")):
        return "plugin_presets"

    if lowered.endswith(".amxd"):
        return "user_library_max_for_live"

    return None


def _path_matches_category(path, category):
    if category == "user_library_max_for_live":
        return _path_matches_stem_set(
            path, _get_user_library_max_for_live_stems()
        ) or _category_for_user_library_path(path) == category
    if category == "max_for_live":
        return not _path_matches_stem_set(
            path, _get_user_library_max_for_live_stems()
        )
    if category not in USER_LIBRARY_CATEGORIES:
        return True
    return _category_for_user_library_path(path) == category


def _resolve_user_library_file(root, browser_path, category):
    relative_path = _safe_browser_relative_path(browser_path)
    if not relative_path:
        return None
    if category not in USER_LIBRARY_CATEGORIES:
        return None

    root_path = Path(root)
    candidate = root_path.joinpath(*relative_path.split("/"))
    if not browser_metadata.is_supported_file_backed_extension(candidate):
        return None

    candidate_resolved = _canonical_inside_root(root_path, candidate)
    if candidate_resolved is None or not candidate_resolved.is_file():
        return None

    root_resolved = Path(root_path).resolve(strict=True)
    resolved_relative = _relative_to_root(root_resolved, candidate_resolved)
    if _category_for_user_library_path(resolved_relative) != category:
        return None
    return candidate_resolved


def _safe_missing_user_library_file_expected(root, browser_path, category):
    relative_path = _safe_browser_relative_path(browser_path)
    if not relative_path or category not in USER_LIBRARY_CATEGORIES:
        return False
    candidate = Path(root).joinpath(*relative_path.split("/"))
    if not browser_metadata.is_supported_file_backed_extension(candidate):
        return False
    existing_parent = candidate.parent
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if _canonical_inside_root(root, existing_parent) is None:
        return False
    return _category_for_user_library_path(relative_path) == category


def _build_user_library_amxd_stem_index(root):
    root_path = Path(root)
    if not root_path.is_dir():
        return {}
    index = {}
    try:
        root_resolved = root_path.resolve(strict=True)
        for candidate in root_path.rglob("*.amxd"):
            stem = _max_for_live_stem(candidate.name)
            if not stem:
                continue
            candidate_resolved = _canonical_inside_root(root_path, candidate)
            if candidate_resolved is None or not candidate_resolved.is_file():
                continue
            resolved_relative = _relative_to_root(root_resolved, candidate_resolved)
            if _category_for_user_library_path(resolved_relative) == "user_library_max_for_live":
                index.setdefault(stem, []).append(candidate_resolved)
    except Exception as exc:
        logger.warning("Failed to scan User Library Max for Live files: %s", exc)
    return index


def _find_user_library_amxd_stem_matches(root, browser_path, stem_index=None):
    target_stem = _max_for_live_stem(browser_path)
    if not target_stem:
        return []
    if stem_index is None:
        stem_index = _build_user_library_amxd_stem_index(root)
    return list(stem_index.get(target_stem, ()))


def _base_metadata_item(browser_path, metadata_status, file_backed_expected, file_exists):
    safe_path = _safe_output_browser_path(browser_path)
    return {
        "browser_path": safe_path,
        "name": safe_path.rsplit("/", 1)[-1],
        "metadata_status": metadata_status,
        "file_backed_expected": bool(file_backed_expected),
        "file_exists": bool(file_exists),
    }


def _file_metadata_status(metadata):
    return "file_backed" if metadata.get("file_id") else "hash_only"


def _metadata_item_for_browser_path(root, category, browser_path, hash_budget, max_for_live_stem_index=None):
    safe_path = _normalise_browser_path(browser_path)
    output_path = _safe_output_browser_path(browser_path)
    if not root or category not in USER_LIBRARY_CATEGORIES:
        return _base_metadata_item(output_path, "path_only", False, False)

    resolved = _resolve_user_library_file(root, safe_path, category)
    if resolved is not None:
        metadata = browser_metadata.metadata_for_file(
            resolved,
            category,
            safe_path,
            hash_budget=hash_budget,
        )
        if metadata:
            status = _file_metadata_status(metadata)
            item = _base_metadata_item(output_path, status, True, True)
            item.update(metadata)
            item["browser_path"] = output_path
            item["name"] = output_path.rsplit("/", 1)[-1]
            item["metadata_status"] = status
            item["file_backed_expected"] = True
            item["file_exists"] = True
            return item

    if category == "user_library_max_for_live":
        matches = _find_user_library_amxd_stem_matches(
            root, safe_path, stem_index=max_for_live_stem_index
        )
        if len(matches) == 1:
            metadata = browser_metadata.metadata_for_file(
                matches[0],
                category,
                safe_path,
                hash_budget=hash_budget,
            )
            if metadata:
                status = _file_metadata_status(metadata)
                item = _base_metadata_item(output_path, status, True, True)
                item.update(metadata)
                item["browser_path"] = output_path
                item["name"] = output_path.rsplit("/", 1)[-1]
                item["metadata_status"] = status
                item["file_backed_expected"] = True
                item["file_exists"] = True
                return item
        if len(matches) > 1:
            return _base_metadata_item(output_path, "ambiguous_file_match", False, False)

    if _safe_missing_user_library_file_expected(root, safe_path, category):
        return _base_metadata_item(output_path, "stale_missing_file", True, False)

    return _base_metadata_item(output_path, "path_only", False, False)


def _json_payload_size(payload):
    return len(_json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _metadata_for_category_items(browser, category, offset=0, limit=MAX_METADATA_PAGE_LIMIT, paths=None):
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = MAX_METADATA_PAGE_LIMIT
    limit = max(0, min(limit, MAX_METADATA_PAGE_LIMIT))

    all_paths = list(paths) if paths is not None else _collect_category_items(browser, category)
    total = len(all_paths)
    if limit == 0:
        return {
            "category": category,
            "offset": offset,
            "limit": limit,
            "total": total,
            "next_offset": offset + 1 if offset < total else None,
            "items": [],
        }
    selected_paths = all_paths[offset:offset + limit]
    root = _bridge_user_library_root()
    max_for_live_stem_index = None
    if root and category == "user_library_max_for_live":
        max_for_live_stem_index = _build_user_library_amxd_stem_index(root)
    hash_budget = {
        "remaining_bytes": browser_metadata.MAX_METADATA_HASH_BYTES_PER_CALL,
    }
    payload = {
        "category": category,
        "offset": offset,
        "limit": limit,
        "total": total,
        "next_offset": None,
        "items": [],
    }

    consumed = 0
    for path in selected_paths:
        item = _metadata_item_for_browser_path(
            root,
            category,
            path,
            hash_budget,
            max_for_live_stem_index=max_for_live_stem_index,
        )
        candidate = dict(payload)
        candidate["items"] = list(payload["items"]) + [item]
        if _json_payload_size(candidate) > MAX_METADATA_PAGE_BYTES and payload["items"]:
            break
        payload["items"].append(item)
        consumed += 1

    next_offset = offset + consumed
    if next_offset < total:
        payload["next_offset"] = next_offset
    return payload


def _path_matches_installed_max_for_live(path):
    return _path_matches_stem_set(path, _get_installed_max_for_live_stems())


def _collect_loadable(root, prefix, depth, results, category=None):
    """Recursively collect loadable items from a browser tree node.

    Args:
        root: A BrowserItem or BrowserItemVector (iterable of BrowserItem).
        prefix: Path prefix built from parent names (e.g. "Wavetable").
        depth: Current recursion depth.
        results: List to append "parent/child" path strings into.
        category: Category key used to classify user library paths.
    """
    if depth > MAX_DEPTH:
        return
    children = _get_children(root)
    for child in children:
        try:
            name = child.name
        except Exception:
            continue
        path = "%s/%s" % (prefix, name) if prefix else name
        try:
            is_loadable = child.is_loadable
        except Exception:
            is_loadable = False
        if is_loadable and _path_matches_category(path, category):
            results.append(path)
        try:
            is_folder = child.is_folder
        except Exception:
            is_folder = False
        if is_folder:
            _collect_loadable(child, path, depth + 1, results, category)


def _extend_unique(results, items):
    seen = set(results)
    for item in items:
        if item not in seen:
            seen.add(item)
            results.append(item)


def _extend_unique_max_for_live(results, items):
    seen = {_max_for_live_stem(item) for item in results}
    for item in items:
        stem = _max_for_live_stem(item)
        if stem not in seen:
            seen.add(stem)
            results.append(item)


def _collect_category_items(browser, category):
    if category == "user_library_max_for_live":
        attr_names = ("max_for_live", "user_library")
    elif category == "max_for_live":
        attr_names = MAX_FOR_LIVE_COMPOSITE_ATTRS
    else:
        attr_names = (CATEGORY_MAP.get(category),)

    results = []
    for attr_name in attr_names:
        if not attr_name or not hasattr(browser, attr_name):
            continue
        try:
            root = getattr(browser, attr_name)
        except Exception as exc:
            logger.error("browser.%s access failed: %s", attr_name, exc)
            continue
        collected = []
        _collect_loadable(root, "", 0, collected, category)
        if category == "max_for_live" and attr_name != "max_for_live":
            collected = [
                item for item in collected
                if _path_matches_installed_max_for_live(item)
            ]
        if category in ("user_library_max_for_live", "max_for_live"):
            _extend_unique_max_for_live(results, collected)
        else:
            _extend_unique(results, collected)
    return results


def _find_by_path(root, segments):
    """Walk the browser tree following exact path segments.

    Args:
        root: The category root (BrowserItemVector or BrowserItem).
        segments: List of path parts, e.g. ["Wavetable", "Warm Pad"].

    Returns:
        The matching BrowserItem, or None.
    """
    children = _get_children(root)
    if not segments:
        return None

    # Find the first segment among the root's children
    found = None
    for child in children:
        try:
            if child.name == segments[0]:
                found = child
                break
        except Exception:
            continue

    if found is None:
        return None
    if len(segments) == 1:
        return found

    # Recurse into the found item for remaining segments
    return _find_by_path(found, segments[1:])


def _find_by_name(root, name, depth):
    """Recursively search for the first loadable item matching name (case-insensitive).

    Args:
        root: A BrowserItem or BrowserItemVector to search within.
        name: The bare name to match (case-insensitive).
        depth: Current recursion depth.

    Returns:
        The matching BrowserItem, or None.
    """
    if depth > MAX_DEPTH:
        return None
    children = _get_children(root)
    for child in children:
        try:
            child_name = child.name
        except Exception:
            continue
        try:
            if child.is_loadable and child_name.lower() == name.lower():
                return child
        except Exception:
            pass
        try:
            if child.is_folder:
                result = _find_by_name(child, name, depth + 1)
                if result is not None:
                    return result
        except Exception:
            pass
    return None


def _find_by_path_with_path(root, segments, prefix=""):
    children = _get_children(root)
    if not segments:
        return None, ""
    for child in children:
        try:
            child_name = child.name
        except Exception:
            continue
        path = "%s/%s" % (prefix, child_name) if prefix else child_name
        if child_name == segments[0]:
            if len(segments) == 1:
                return child, path
            return _find_by_path_with_path(child, segments[1:], path)
    return None, ""


def _find_by_name_with_path(root, name, depth, prefix="", category=None):
    if depth > MAX_DEPTH:
        return None, ""
    children = _get_children(root)
    for child in children:
        try:
            child_name = child.name
        except Exception:
            continue
        path = "%s/%s" % (prefix, child_name) if prefix else child_name
        try:
            is_loadable = child.is_loadable
        except Exception:
            is_loadable = False
        if (is_loadable and child_name.lower() == name.lower()
                and _path_matches_category(path, category)):
            return child, path
        try:
            is_folder = child.is_folder
        except Exception:
            is_folder = False
        if is_folder:
            result, result_path = _find_by_name_with_path(
                child, name, depth + 1, path, category
            )
            if result is not None:
                return result, result_path
    return None, ""


def _find_loadable_in_root(root, item_query, category):
    if "/" in item_query:
        target, path = _find_by_path_with_path(root, item_query.split("/"))
    else:
        target, path = _find_by_name_with_path(root, item_query, 0, category=category)
    if target is None:
        return None, ""
    if not _path_matches_category(path, category):
        return None, ""
    return target, path


def _find_loadable_for_category(root, item_query, category):
    target, _path = _find_loadable_in_root(root, item_query, category)
    return target


def _find_loadable_for_browser_category(browser, item_query, category):
    if category == "user_library_max_for_live":
        attr_names = ("max_for_live", "user_library")
    elif category == "max_for_live":
        attr_names = MAX_FOR_LIVE_COMPOSITE_ATTRS
    else:
        attr_names = (CATEGORY_MAP.get(category),)

    for attr_name in attr_names:
        if not attr_name or not hasattr(browser, attr_name):
            continue
        try:
            root = getattr(browser, attr_name)
        except Exception:
            continue
        target, path = _find_loadable_in_root(root, item_query, category)
        if target is None:
            continue
        if (
            category == "max_for_live"
            and attr_name != "max_for_live"
            and not _path_matches_installed_max_for_live(path)
        ):
            continue
        return target
    return None


class BrowserHandler(AbletonOSCHandler):
    def __init__(self, manager):
        super().__init__(manager)
        self.class_identifier = "browser"

    def _get_browser(self):
        """Return the Application.browser object, or None if unavailable."""
        try:
            app = Live.Application.get_application()
        except Exception as e:
            logger.error("Failed to get Application: %s" % e)
            return None
        if not hasattr(app, "browser"):
            logger.warning("Application has no 'browser' attribute")
            return None
        return app.browser

    def init_api(self):
        logger.info("BrowserHandler: registering endpoints")

        # ------------------------------------------------------------------
        # /live/browser/get/capabilities
        # ------------------------------------------------------------------
        @guarded_lom("browser_get_capabilities")
        def get_capabilities(params):
            logger.info("browser/get/capabilities called")
            browser = self._get_browser()
            if browser is None:
                return ("unsupported",)
            supported = []
            for category_key, attr_name in CATEGORY_MAP.items():
                if category_key == "max_for_live" and not _max_for_live_supported(browser):
                    continue
                if hasattr(browser, attr_name):
                    supported.append(category_key)
            if not supported:
                return ("unsupported",)
            logger.info("browser capabilities: %s" % str(supported))
            return tuple(supported)

        self.osc_server.add_handler("/live/browser/get/capabilities", get_capabilities)

        # ------------------------------------------------------------------
        # /live/browser/get/names  (category_str)
        # ------------------------------------------------------------------
        @guarded_lom("browser_get_names")
        def get_names(params):
            if not params or len(params) < 1:
                return ("error: category parameter required",)
            category_str = str(params[0])
            logger.info("browser/get/names called: category=%s" % category_str)

            browser = self._get_browser()
            if browser is None:
                return ("error: browser API not available",)

            unsupported_error = _max_for_live_unsupported_error(category_str, browser)
            if unsupported_error is not None:
                return unsupported_error

            attr_name = CATEGORY_MAP.get(category_str)
            if attr_name is None:
                return ("error: unknown category '%s'" % category_str,)
            if not hasattr(browser, attr_name):
                return ("error: category '%s' not supported" % category_str,)

            results = _collect_category_items(browser, category_str)
            total = len(results)
            logger.info("browser/get/names: found %d items in %s" % (total, category_str))
            if not results:
                return (category_str, "empty")
            # Cap results to avoid exceeding UDP packet size limit
            if total > MAX_RESULTS:
                results = results[:MAX_RESULTS]
                results.append("TRUNCATED: showing %d of %d items — use search_browser_items to find specific items" % (MAX_RESULTS, total))
            # Echo category_str first so Ohmic's response key matching works
            return (category_str, *results)

        self.osc_server.add_handler("/live/browser/get/names", get_names)

        # ------------------------------------------------------------------
        # /live/browser/get/metadata_page  (category_str, offset=0, limit=25)
        # ------------------------------------------------------------------
        @guarded_lom_json("browser_get_metadata_page")
        def get_metadata_page(params):
            if not params or len(params) < 1:
                return (_json.dumps({"error": "category parameter required"}),)
            category_str = str(params[0])
            try:
                offset = int(params[1]) if len(params) >= 2 else 0
            except (ValueError, TypeError):
                return (_json.dumps({"error": "offset must be an integer"}),)
            try:
                limit = int(params[2]) if len(params) >= 3 else MAX_METADATA_PAGE_LIMIT
            except (ValueError, TypeError):
                return (_json.dumps({"error": "limit must be an integer"}),)

            browser = self._get_browser()
            if browser is None:
                return (_json.dumps({"error": "browser API not available"}),)

            unsupported_error = _max_for_live_unsupported_error(category_str, browser)
            if unsupported_error is not None:
                return (_json.dumps({"error": str(unsupported_error[0])}),)

            attr_name = CATEGORY_MAP.get(category_str)
            if attr_name is None:
                return (_json.dumps({"error": "unknown category '%s'" % category_str}),)
            if not hasattr(browser, attr_name):
                return (_json.dumps({"error": "category '%s' not supported" % category_str}),)

            payload = _metadata_for_category_items(
                browser,
                category_str,
                offset=offset,
                limit=limit,
            )
            return (_json.dumps(payload, sort_keys=True, separators=(",", ":")),)

        self.osc_server.add_handler(
            "/live/browser/get/metadata_page", get_metadata_page
        )

        # ------------------------------------------------------------------
        # /live/browser/load  (track_index, category_str, item_name_or_path)
        # ------------------------------------------------------------------
        @guarded_lom("browser_load_item")
        def load_item(params):
            if not params or len(params) < 3:
                return ("error: requires track_index, category, item_name_or_path",)

            try:
                track_index = int(params[0])
            except (ValueError, TypeError):
                return ("error: track_index must be an integer",)
            category_str = str(params[1])
            item_query = str(params[2])
            positioned_load = len(params) >= 4
            insert_mode = str(params[3]).strip().lower() if positioned_load else ""
            anchor_device_index = -1
            if positioned_load and len(params) >= 5:
                try:
                    anchor_device_index = int(params[4])
                except (ValueError, TypeError):
                    return (track_index, category_str, item_query, insert_mode,
                            -1, "error: device_index must be an integer")
            logger.info("browser/load called: track=%d, category=%s, item=%s"
                        % (track_index, category_str, item_query))

            insert_mode_values = {
                "append": 0,
                "before": 1,
                "after": 2,
            }

            def load_error(message):
                if positioned_load:
                    return (track_index, category_str, item_query, insert_mode,
                            anchor_device_index, "error: " + str(message))
                return ("error: " + str(message),)

            if positioned_load and insert_mode not in insert_mode_values:
                return load_error("insert_mode must be append, before, or after")
            if (positioned_load and insert_mode in ("before", "after") and
                    anchor_device_index < 0):
                return load_error("before/after insert_mode requires device_index")

            browser = self._get_browser()
            if browser is None:
                return load_error("browser API not available")

            unsupported_error = _max_for_live_unsupported_error(category_str, browser)
            if unsupported_error is not None:
                if positioned_load:
                    return load_error(str(unsupported_error[0]).replace("error: ", "", 1))
                return unsupported_error

            if not hasattr(browser, "load_item"):
                return load_error("browser.load_item not available")

            attr_name = CATEGORY_MAP.get(category_str)
            if attr_name is None:
                return load_error("unknown category '%s'" % category_str)
            if not hasattr(browser, attr_name):
                return load_error("category '%s' not supported" % category_str)

            target = _find_loadable_for_browser_category(
                browser, item_query, category_str
            )

            if target is None:
                return load_error("item '%s' not found in %s" % (item_query, category_str))

            try:
                is_loadable = target.is_loadable
            except Exception:
                is_loadable = False
            if not is_loadable:
                return load_error("item '%s' is not loadable (it may be a folder)" % item_query)

            # Select the target track
            try:
                tracks = self.song.tracks
                if track_index < 0 or track_index >= len(tracks):
                    return ("error: track_index %d out of range (0-%d)" % (track_index, len(tracks) - 1),)
                track = tracks[track_index]
                self.song.view.selected_track = track
            except Exception as e:
                logger.error("Failed to select track %d: %s" % (track_index, e))
                return load_error("failed to select track %d" % track_index)

            # Count devices before loading (skip for presets — they modify
            # an existing device rather than adding a new one)
            previous_insert_mode = None
            if positioned_load:
                try:
                    if not hasattr(track, "view"):
                        return load_error("track.view is not available")
                    if not hasattr(track.view, "device_insert_mode"):
                        return load_error("device_insert_mode not available")
                    previous_insert_mode = track.view.device_insert_mode
                    if insert_mode in ("before", "after"):
                        device_count = len(track.devices)
                        if (anchor_device_index < 0 or
                                anchor_device_index >= device_count):
                            return load_error(
                                "device_index %d out of range (0-%d)"
                                % (anchor_device_index, device_count - 1)
                            )
                        self.song.view.select_device(
                            track.devices[anchor_device_index]
                        )
                    track.view.device_insert_mode = insert_mode_values[insert_mode]
                except Exception as e:
                    logger.error("Failed to prepare positioned browser load: %s" % e)
                    return load_error(
                        "failed to prepare positioned load: %s" % str(e)
                    )

            is_preset = _is_preset_category(category_str)
            device_count_before = -1
            before_names = _device_names(track) if positioned_load else ()
            if not is_preset:
                try:
                    device_count_before = len(track.devices)
                except Exception:
                    pass

            # Load the item
            try:
                browser.load_item(target)
                logger.info("browser.load_item succeeded for '%s'" % item_query)
            except Exception as e:
                logger.error("browser.load_item failed: %s" % e)
                return load_error("load_item failed: %s" % str(e))
            finally:
                if positioned_load and previous_insert_mode is not None:
                    try:
                        track.view.device_insert_mode = previous_insert_mode
                    except Exception as e:
                        logger.warning("Failed to restore device_insert_mode: %s" % e)

            # Verify device count increased (only for non-preset categories)
            after_names = _device_names(track) if positioned_load else ()
            if device_count_before >= 0:
                try:
                    device_count_after = len(track.devices)
                except Exception:
                    device_count_after = -1
                if device_count_after >= 0 and device_count_after <= device_count_before:
                    logger.warning("Device count did not increase after load_item "
                                   "(before=%d, after=%d)" % (device_count_before, device_count_after))
                    if positioned_load:
                        actual_index = _find_inserted_device_index(
                            before_names, after_names
                        )
                        return (track_index, category_str, item_query,
                                insert_mode, anchor_device_index,
                                "warning: load_item completed but device count unchanged - "
                                "item may not have loaded correctly",
                                actual_index, *after_names)
                    return ("warning: load_item completed but device count unchanged — "
                            "item may not have loaded correctly",)

            if positioned_load:
                actual_index = _find_inserted_device_index(before_names, after_names)
                return (track_index, category_str, item_query, insert_mode,
                        anchor_device_index, "ok", actual_index, *after_names)

            # Echo request params first so Ohmic's prefix key matching works
            return (track_index, category_str, item_query, "ok")

        self.osc_server.add_handler("/live/browser/load", load_item)

        # ------------------------------------------------------------------
        # /live/browser/search  (category_str, query_str)
        # ------------------------------------------------------------------
        @guarded_lom("browser_search_items")
        def search_items(params):
            if not params or len(params) < 2:
                return ("error: requires category and query",)
            category_str = str(params[0])
            query_str = str(params[1]).lower()
            logger.info("browser/search called: category=%s, query=%s"
                        % (category_str, query_str))

            browser = self._get_browser()
            if browser is None:
                return ("error: browser API not available",)

            unsupported_error = _max_for_live_unsupported_error(category_str, browser)
            if unsupported_error is not None:
                return unsupported_error

            attr_name = CATEGORY_MAP.get(category_str)
            if attr_name is None:
                return ("error: unknown category '%s'" % category_str,)
            if not hasattr(browser, attr_name):
                return ("error: category '%s' not supported" % category_str,)

            all_items = _collect_category_items(browser, category_str)
            matches = [item for item in all_items if query_str in item.lower()]
            logger.info("browser/search: %d matches for '%s' in %s (out of %d)"
                        % (len(matches), query_str, category_str, len(all_items)))
            if not matches:
                return (category_str, query_str, "no matches")
            if len(matches) > MAX_RESULTS:
                matches = matches[:MAX_RESULTS]
            # Echo category and query so response key matching works
            return (category_str, query_str, *matches)

        self.osc_server.add_handler("/live/browser/search", search_items)
        logger.info("BrowserHandler: all endpoints registered")
