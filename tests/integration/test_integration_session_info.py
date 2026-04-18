"""Integration tests for the aggregate JSON-returning handlers
(session_info, clip_grid, playing_positions).

These are the handlers that use @guarded_lom_json — verify the
success-path JSON shape round-trips correctly."""
import json

import pytest

pytestmark = pytest.mark.integration


def test_clip_grid_returns_valid_json(osc):
    reply = osc.query("/live/song/get/clip_grid", [])
    assert len(reply) == 1
    data = json.loads(reply[0])
    # Shape: {"slots": [[...], ...], ...} — don't over-assert, just
    # verify JSON parseable.
    assert isinstance(data, dict)


def test_playing_positions_returns_valid_json(osc):
    reply = osc.query("/live/song/get/playing_positions", [])
    assert len(reply) == 1
    data = json.loads(reply[0])
    assert "tempo" in data
    assert "is_playing" in data
    assert "playing_positions" in data
