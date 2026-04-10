import Live
import logging
from typing import Tuple, Optional
from .handler import AbletonOSCHandler

logger = logging.getLogger("abletonosc")

# Maps category strings to browser property names.
# Only categories where hasattr(browser, name) is True will be reported as supported.
CATEGORY_MAP = {
    "instruments": "instruments",
    "audio_effects": "audio_effects",
    "midi_effects": "midi_effects",
    "plugins": "plugins",
    "user_library": "user_library",
    "presets": "user_library",
}

# Extension filters per category.  Tuple of lowercase suffixes to match.
# user_library → .adg (racks), presets → .adv (native) + .vstpreset (VST3).
_EXT_FILTERS = {
    "user_library": (".adg",),
    "presets": (".adv", ".vstpreset", ".aupreset"),
}

MAX_DEPTH = 3
MAX_RESULTS = 500


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


def _collect_loadable(root, prefix, depth, results, ext_filter=None):
    """Recursively collect loadable items from a browser tree node.

    Args:
        root: A BrowserItem or BrowserItemVector (iterable of BrowserItem).
        prefix: Path prefix built from parent names (e.g. "Wavetable").
        depth: Current recursion depth.
        results: List to append "parent/child" path strings into.
        ext_filter: If set, only include items whose name ends with one of
                    these extensions.  Accepts a string or tuple of strings
                    (e.g. (".adv", ".vstpreset")).
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
            if child.is_loadable:
                if ext_filter is None or name.lower().endswith(ext_filter):
                    results.append(path)
        except Exception:
            pass
        try:
            if child.is_folder:
                _collect_loadable(child, path, depth + 1, results, ext_filter)
        except Exception:
            pass


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
        def get_capabilities(params):
            logger.info("browser/get/capabilities called")
            browser = self._get_browser()
            if browser is None:
                return ("unsupported",)
            supported = []
            for category_key, attr_name in CATEGORY_MAP.items():
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
        def get_names(params):
            if not params or len(params) < 1:
                return ("error: category parameter required",)
            category_str = str(params[0])
            logger.info("browser/get/names called: category=%s" % category_str)

            browser = self._get_browser()
            if browser is None:
                return ("error: browser API not available",)

            attr_name = CATEGORY_MAP.get(category_str)
            if attr_name is None:
                return ("error: unknown category '%s'" % category_str,)
            if not hasattr(browser, attr_name):
                return ("error: category '%s' not supported" % category_str,)

            try:
                root = getattr(browser, attr_name)
                logger.info("browser.%s type: %s" % (attr_name, type(root).__name__))
            except Exception as e:
                logger.error("browser.%s access failed: %s" % (attr_name, e))
                return ("error: failed to access category '%s'" % category_str,)

            results = []
            ext_filter = _EXT_FILTERS.get(category_str)
            _collect_loadable(root, "", 0, results, ext_filter)
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
            if not hasattr(browser, "load_item"):
                return ("error: browser.load_item not available",)

            attr_name = CATEGORY_MAP.get(category_str)
            if attr_name is None:
                return ("error: unknown category '%s'" % category_str,)
            if not hasattr(browser, attr_name):
                return ("error: category '%s' not supported" % category_str,)

            try:
                root = getattr(browser, attr_name)
            except Exception as e:
                logger.error("browser.%s access failed: %s" % (attr_name, e))
                return ("error: failed to access category '%s'" % category_str,)

            # Path-based resolution (contains "/") vs bare name fallback
            if "/" in item_query:
                segments = item_query.split("/")
                target = _find_by_path(root, segments)
            else:
                target = _find_by_name(root, item_query, 0)

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
            is_preset = category_str == "presets"
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

            attr_name = CATEGORY_MAP.get(category_str)
            if attr_name is None:
                return ("error: unknown category '%s'" % category_str,)
            if not hasattr(browser, attr_name):
                return ("error: category '%s' not supported" % category_str,)

            try:
                root = getattr(browser, attr_name)
            except Exception as e:
                logger.error("browser.%s access failed: %s" % (attr_name, e))
                return ("error: failed to access category '%s'" % category_str,)

            all_items = []
            ext_filter = _EXT_FILTERS.get(category_str)
            _collect_loadable(root, "", 0, all_items, ext_filter)
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
