import Live
import logging
import os
import sys
from typing import Tuple, Optional
from .handler import AbletonOSCHandler, guarded_lom

logger = logging.getLogger("abletonosc")

# Maps category strings to browser property names.
# Only categories where hasattr(browser, name) is True will be reported as supported.
CATEGORY_MAP = {
    "instruments": "instruments",
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


def _max_for_live_supported(browser):
    return hasattr(browser, "max_for_live")


def _max_for_live_unsupported_error(category, browser):
    if category == "max_for_live" and not _max_for_live_supported(browser):
        return (MAX_FOR_LIVE_UNSUPPORTED_ERROR,)
    return None


MAX_DEPTH = 5
MAX_RESULTS = 500
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
            logger.info("browser/load called: track=%d, category=%s, item=%s"
                        % (track_index, category_str, item_query))

            browser = self._get_browser()
            if browser is None:
                return ("error: browser API not available",)

            unsupported_error = _max_for_live_unsupported_error(category_str, browser)
            if unsupported_error is not None:
                return unsupported_error

            if not hasattr(browser, "load_item"):
                return ("error: browser.load_item not available",)

            attr_name = CATEGORY_MAP.get(category_str)
            if attr_name is None:
                return ("error: unknown category '%s'" % category_str,)
            if not hasattr(browser, attr_name):
                return ("error: category '%s' not supported" % category_str,)

            target = _find_loadable_for_browser_category(
                browser, item_query, category_str
            )

            if target is None:
                return ("error: item '%s' not found in %s" % (item_query, category_str),)

            try:
                is_loadable = target.is_loadable
            except Exception:
                is_loadable = False
            if not is_loadable:
                return ("error: item '%s' is not loadable (it may be a folder)" % item_query,)

            # Select the target track
            try:
                tracks = self.song.tracks
                if track_index < 0 or track_index >= len(tracks):
                    return ("error: track_index %d out of range (0-%d)" % (track_index, len(tracks) - 1),)
                self.song.view.selected_track = tracks[track_index]
            except Exception as e:
                logger.error("Failed to select track %d: %s" % (track_index, e))
                return ("error: failed to select track %d" % track_index,)

            # Count devices before loading (skip for presets — they modify
            # an existing device rather than adding a new one)
            is_preset = _is_preset_category(category_str)
            device_count_before = -1
            if not is_preset:
                try:
                    device_count_before = len(tracks[track_index].devices)
                except Exception:
                    pass

            # Load the item
            try:
                browser.load_item(target)
                logger.info("browser.load_item succeeded for '%s'" % item_query)
            except Exception as e:
                logger.error("browser.load_item failed: %s" % e)
                return ("error: load_item failed: %s" % str(e),)

            # Verify device count increased (only for non-preset categories)
            if device_count_before >= 0:
                try:
                    device_count_after = len(tracks[track_index].devices)
                except Exception:
                    device_count_after = -1
                if device_count_after >= 0 and device_count_after <= device_count_before:
                    logger.warning("Device count did not increase after load_item "
                                   "(before=%d, after=%d)" % (device_count_before, device_count_after))
                    return ("warning: load_item completed but device count unchanged — "
                            "item may not have loaded correctly",)

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
