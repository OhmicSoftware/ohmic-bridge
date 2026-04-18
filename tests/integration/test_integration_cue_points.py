"""Integration tests for the song_cue_points capability bucket."""
import pytest
from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


def test_cue_points_list_is_returnable(osc):
    # The project may or may not have cue points. Endpoint must not
    # raise either way.
    reply = osc.query("/live/song/get/cue_points", [])
    assert isinstance(reply, tuple)
