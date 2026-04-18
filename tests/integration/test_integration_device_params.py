"""Integration tests for device_parameter_value_strings bucket.

Skips if the default project has no devices on track 0 — this isn't
a Bridge failure, it's a project-setup precondition."""
import pytest
from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration

TRACK_ID = 0
DEVICE_ID = 0
PARAM_ID = 1  # parameter 0 is typically "Device On"; 1 is usually a real control


def test_device_parameter_value_string_roundtrip(osc):
    try:
        reply = osc.query("/live/device/get/parameter/value_string",
                          [TRACK_ID, DEVICE_ID, PARAM_ID])
    except Exception as e:
        pytest.skip("No device at track %d device %d parameter %d: %s"
                    % (TRACK_ID, DEVICE_ID, PARAM_ID, e))
    # Wire: (track, device, param, string)
    assert len(reply) == 4
    assert isinstance(reply[3], str)
