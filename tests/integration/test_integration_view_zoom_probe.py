"""Integration coverage for the diagnostic Application.View.zoom_view probe."""

import pytest

pytestmark = pytest.mark.integration


def test_view_zoom_view_diagnostic_probe_replies(osc):
    """The diagnostic zoom probe returns a bounded result tuple instead
    of timing out, whether Live accepts or rejects the zoom command."""
    reply = osc.query("/live/view/probe/zoom_view", [3, "Arranger", 0, 1])
    assert len(reply) == 4, (
        "zoom_view diagnostic must return "
        "(attempts_requested, attempts_completed, errors_seen, first_error), "
        "got %r" % (reply,)
    )
    attempts_requested = int(reply[0])
    attempts_completed = int(reply[1])
    errors_seen = int(reply[2])
    first_error = str(reply[3])

    assert attempts_requested == 1
    assert attempts_completed in (0, 1)
    assert errors_seen in (0, 1)
    assert attempts_completed + errors_seen == 1
    if errors_seen:
        assert first_error
    else:
        assert first_error == ""
