"""Integration tests for scene_tempo and scene_time_signature buckets."""
import pytest
from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration

SCENE_ID = 0


def test_scene_tempo_roundtrip(osc):
    osc.send_message("/live/scene/set/tempo", [SCENE_ID, 128.5])
    wait_one_tick()
    reply = osc.query("/live/scene/get/tempo", [SCENE_ID])
    assert reply[0] == SCENE_ID
    assert reply[1] == pytest.approx(128.5)


def test_scene_time_signature_roundtrip(osc):
    osc.send_message("/live/scene/set/time_signature_numerator", [SCENE_ID, 7])
    osc.send_message("/live/scene/set/time_signature_denominator", [SCENE_ID, 8])
    wait_one_tick()
    num = osc.query("/live/scene/get/time_signature_numerator", [SCENE_ID])
    den = osc.query("/live/scene/get/time_signature_denominator", [SCENE_ID])
    assert num[1] == 7
    assert den[1] == 8
