"""Integration tests for the browser capability bucket."""
import pytest

pytestmark = pytest.mark.integration


def test_browser_capabilities_returnable(osc):
    """browser/get/capabilities returns a flat tuple of supported
    category names. The handler source (abletonosc/browser.py)
    returns ``tuple(supported)`` where supported is a list of strings
    drawn from CATEGORY_MAP keys, or ``("unsupported",)`` when the
    browser subsystem is absent. Assert every element is one of the
    known strings — no mixed types, no trailing integers."""
    reply = osc.query("/live/browser/get/capabilities", [])
    allowed = {
        "instruments", "audio_effects", "midi_effects",
        "plugins", "user_library", "presets", "unsupported",
    }
    assert len(reply) >= 1, "capabilities reply was empty"
    for category in reply:
        assert isinstance(category, str), (
            "every element must be a string; got %r in %r" % (category, reply)
        )
        assert category in allowed, (
            "unknown category %r in %r — update the allow-list if the "
            "Bridge added a new category" % (category, reply)
        )


def test_browser_get_names_for_instruments(osc):
    reply = osc.query("/live/browser/get/names", ["instruments"])
    # Reply shape: (category_name, name1, name2, ...). Must have at
    # least the echoed category name.
    assert len(reply) >= 1
    assert reply[0] == "instruments"
