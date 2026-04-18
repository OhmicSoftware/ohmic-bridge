"""Integration tests for the browser capability bucket."""
import pytest

pytestmark = pytest.mark.integration


def test_browser_capabilities_returnable(osc):
    reply = osc.query("/live/browser/get/capabilities", [])
    assert len(reply) == 1
    # Reply is a JSON string listing category support.
    import json
    data = json.loads(reply[0])
    assert isinstance(data, dict)


def test_browser_get_names_for_instruments(osc):
    reply = osc.query("/live/browser/get/names", ["instruments"])
    # Reply shape: (category_name, name1, name2, ...). Must have at
    # least the echoed category name.
    assert len(reply) >= 1
    assert reply[0] == "instruments"
