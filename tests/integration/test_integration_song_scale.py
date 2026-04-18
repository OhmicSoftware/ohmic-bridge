"""Integration tests for the song_scale_properties capability bucket."""
import json

import pytest
from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


def test_root_note_roundtrip(osc):
    osc.send_message("/live/song/set/root_note", [7])  # G
    wait_one_tick()
    reply = osc.query("/live/song/get/root_note", [])
    assert reply[-1] == 7


def test_scale_name_roundtrip(osc):
    osc.send_message("/live/song/set/scale_name", ["Minor"])
    wait_one_tick()
    reply = osc.query("/live/song/get/scale_name", [])
    assert reply[-1] == "Minor"


def test_session_info_returns_valid_json_with_expected_keys(osc):
    reply = osc.query("/live/song/get/session_info", [])
    assert len(reply) == 1
    data = json.loads(reply[0])
    for key in ("track_names", "track_colors", "midi_tracks",
                "num_scenes", "root_note", "scale_name", "tempo",
                "is_playing", "playing_slots"):
        assert key in data, "session_info missing key: " + key
