"""Integration tests for the browser capability bucket."""
import pytest

pytestmark = pytest.mark.integration


def test_browser_capabilities_returnable(osc):
    """browser/get/capabilities returns a flat tuple of supported
    category names (instruments, audio_effects, midi_effects,
    plugins, user_library, presets). The exact set depends on the
    Live version. A UDP reply may also carry leading indicators (for
    example an echoed request counter) so we just assert the reply
    contains at least one recognizable category name string."""
    reply = osc.query("/live/browser/get/capabilities", [])
    known = {
        "instruments", "audio_effects", "midi_effects",
        "plugins", "user_library", "presets", "unsupported",
    }
    string_parts = [p for p in reply if isinstance(p, str)]
    assert any(p in known for p in string_parts), (
        "reply did not contain any known browser category name: %r" % (reply,)
    )


def test_browser_get_names_for_instruments(osc):
    reply = osc.query("/live/browser/get/names", ["instruments"])
    # Reply shape: (category_name, name1, name2, ...). Must have at
    # least the echoed category name.
    assert len(reply) >= 1
    assert reply[0] == "instruments"
