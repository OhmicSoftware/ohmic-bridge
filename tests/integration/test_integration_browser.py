"""Integration tests for the browser capability bucket."""
import pytest

pytestmark = pytest.mark.integration


def test_browser_capabilities_returnable(osc):
    """browser/get/capabilities returns a flat tuple of supported
    category names (instruments, audio_effects, midi_effects,
    plugins, user_library, presets). The exact set depends on the
    Live version — we just assert the reply is non-empty and every
    element is a string."""
    reply = osc.query("/live/browser/get/capabilities", [])
    assert len(reply) >= 1
    for category in reply:
        assert isinstance(category, str), (
            "expected category name string, got %r" % (category,)
        )


def test_browser_get_names_for_instruments(osc):
    reply = osc.query("/live/browser/get/names", ["instruments"])
    # Reply shape: (category_name, name1, name2, ...). Must have at
    # least the echoed category name.
    assert len(reply) >= 1
    assert reply[0] == "instruments"
