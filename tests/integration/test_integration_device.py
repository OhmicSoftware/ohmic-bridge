"""Integration tests for /live/device/* endpoints.

Covers the device_parameter_value_strings bucket (value_string read),
plus the parameter list reads (parameters/name, parameters/value) and
the individual parameter value round-trip (get/parameter/value,
set/parameter/value).

All tests skip cleanly if there's no device on track 0 device 0 —
that's a project-setup precondition, not a Bridge failure.

do not parallelize — the set/parameter/value round-trip mutates a
device control. Running alongside any test that reads or writes the
same device will thrash state.
"""
import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration

TRACK_ID = 0
DEVICE_ID = 0
PARAM_ID = 1  # parameter 0 is typically "Device On"; 1 is usually a real tunable control


def _has_device(osc):
    """Return True if track TRACK_ID has at least DEVICE_ID+1 devices
    AND that device has at least PARAM_ID+1 parameters. Skip helper."""
    try:
        names_reply = osc.query(
            "/live/device/get/parameters/name", [TRACK_ID, DEVICE_ID],
        )
    except Exception:
        return False
    # Wire: (track, device, name_0, name_1, ...) — need at least
    # PARAM_ID + 1 param names after the echoed (track, device).
    if len(names_reply) < 2 + PARAM_ID + 1:
        return False
    # Guard against an error-reply tuple like ("error: ...",).
    first = names_reply[0]
    if isinstance(first, str) and first.startswith("error:"):
        return False
    return True


def _skip_if_no_device(osc):
    if not _has_device(osc):
        pytest.skip(
            "No device with >= %d parameters at track %d device %d — "
            "device tests need a real device on that slot. Add a stock "
            "instrument (e.g. Operator) and re-run."
            % (PARAM_ID + 1, TRACK_ID, DEVICE_ID)
        )


# --------------------------------------------------------------------------
# value_string read (from existing test_integration_device_params.py)
# --------------------------------------------------------------------------
def test_device_parameter_value_string_roundtrip(osc):
    """/live/device/get/parameter/value_string returns the UI-friendly
    string for a device parameter (e.g. "2500 Hz")."""
    try:
        reply = osc.query(
            "/live/device/get/parameter/value_string",
            [TRACK_ID, DEVICE_ID, PARAM_ID],
        )
    except Exception as e:
        pytest.skip(
            "No device at track %d device %d parameter %d: %s"
            % (TRACK_ID, DEVICE_ID, PARAM_ID, e)
        )
    # Wire: (track, device, param, string)
    assert len(reply) == 4, (
        "value_string reply wire format changed — expected "
        "(track, device, param, string), got %r" % (reply,)
    )
    assert isinstance(reply[3], str)


# --------------------------------------------------------------------------
# parameters/name read (full list)
# --------------------------------------------------------------------------
def test_device_parameters_name_read(osc):
    """/live/device/get/parameters/name returns all parameter names
    on a device as a flat tuple. Wire format from abletonosc/device.py:
      (track_index, device_index, name_0, name_1, ...)
    """
    _skip_if_no_device(osc)

    reply = osc.query(
        "/live/device/get/parameters/name", [TRACK_ID, DEVICE_ID],
    )
    assert len(reply) >= 3, (
        "parameters/name reply must have at least the two echoed "
        "indices plus one name — got %r" % (reply,)
    )
    assert int(reply[0]) == TRACK_ID, (
        "first element must be track index — got %r" % (reply,)
    )
    assert int(reply[1]) == DEVICE_ID, (
        "second element must be device index — got %r" % (reply,)
    )
    for name in reply[2:]:
        assert isinstance(name, str), (
            "parameter name must be a string — got %r (type %s) "
            "in %r"
            % (name, type(name).__name__, reply)
        )


# --------------------------------------------------------------------------
# parameters/value read (full list)
# --------------------------------------------------------------------------
def test_device_parameters_value_read(osc):
    """/live/device/get/parameters/value returns all parameter values
    on a device. Wire format:
      (track_index, device_index, value_0, value_1, ...)
    Values are floats in each parameter's native range (not
    normalized to 0..1 — that's Ohmic's convention, not the Bridge's).
    """
    _skip_if_no_device(osc)

    reply = osc.query(
        "/live/device/get/parameters/value", [TRACK_ID, DEVICE_ID],
    )
    assert len(reply) >= 3, (
        "parameters/value reply must have at least the two echoed "
        "indices plus one value — got %r" % (reply,)
    )
    assert int(reply[0]) == TRACK_ID, (
        "first element must be track index — got %r" % (reply,)
    )
    assert int(reply[1]) == DEVICE_ID, (
        "second element must be device index — got %r" % (reply,)
    )
    for value in reply[2:]:
        assert isinstance(value, (int, float)), (
            "parameter value must be numeric — got %r (type %s) "
            "in %r"
            % (value, type(value).__name__, reply)
        )


# --------------------------------------------------------------------------
# set/parameter/value round-trip
# --------------------------------------------------------------------------
def test_device_set_parameter_value_roundtrip(osc):
    """Save parameter PARAM_ID's current value, set to a distinctive
    value within the parameter's min/max range, verify via read-back,
    restore, verify restore.

    Uses /live/device/get/parameters/min and /parameters/max to pick
    a safe in-range sentinel so the test works regardless of which
    device is loaded on the slot.
    """
    _skip_if_no_device(osc)

    # Read original value + min + max for PARAM_ID. The full-list
    # endpoints echo (track, device, values...) so PARAM_ID maps to
    # reply[2 + PARAM_ID].
    values_reply = osc.query(
        "/live/device/get/parameters/value", [TRACK_ID, DEVICE_ID],
    )
    min_reply = osc.query(
        "/live/device/get/parameters/min", [TRACK_ID, DEVICE_ID],
    )
    max_reply = osc.query(
        "/live/device/get/parameters/max", [TRACK_ID, DEVICE_ID],
    )
    original = float(values_reply[2 + PARAM_ID])
    param_min = float(min_reply[2 + PARAM_ID])
    param_max = float(max_reply[2 + PARAM_ID])

    # Sanity: min < max (otherwise the param isn't tunable — skip).
    if param_min >= param_max:
        pytest.skip(
            "parameter %d on (%d, %d) has min=%r >= max=%r — not a "
            "tunable control (likely a toggle or enum with a single "
            "value). Pick a different param or slot."
            % (PARAM_ID, TRACK_ID, DEVICE_ID, param_min, param_max)
        )

    # Pick a sentinel at ~25% of the range so it's distinct from
    # common defaults (50%) and from the endpoints (0% / 100%).
    sentinel = param_min + 0.25 * (param_max - param_min)

    # If the sentinel happens to match the original within epsilon,
    # pick a different target (75% instead).
    if abs(sentinel - original) < 1e-6:
        sentinel = param_min + 0.75 * (param_max - param_min)

    # Also confirm the single-param getter returns the original
    # before we mutate — proves the endpoint pair is coherent.
    probe = osc.query(
        "/live/device/get/parameter/value",
        [TRACK_ID, DEVICE_ID, PARAM_ID],
    )
    # Wire: (track, device, param, value)
    assert len(probe) == 4, (
        "get/parameter/value reply wire format — expected "
        "(track, device, param, value), got %r" % (probe,)
    )
    assert int(probe[2]) == PARAM_ID
    assert float(probe[3]) == pytest.approx(original), (
        "get/parameter/value and get/parameters/value disagree for "
        "param %d — individual %r vs list %r"
        % (PARAM_ID, probe[3], original)
    )

    try:
        # Act: set the parameter.
        osc.send_message(
            "/live/device/set/parameter/value",
            [TRACK_ID, DEVICE_ID, PARAM_ID, sentinel],
        )
        wait_one_tick()

        # Verify via read-back. Live may quantize the value for
        # quantized (stepped) parameters, so don't demand exact
        # equality — just assert the read-back differs from the
        # original by a meaningful amount (>1% of the range).
        after_set = osc.query(
            "/live/device/get/parameter/value",
            [TRACK_ID, DEVICE_ID, PARAM_ID],
        )
        assert len(after_set) == 4
        after_value = float(after_set[3])
        range_span = param_max - param_min
        delta = abs(after_value - original)
        threshold = 0.01 * range_span
        assert delta > threshold, (
            "set/parameter/value did not meaningfully change param "
            "%d — original %r, after set %r (delta %r, threshold %r)"
            % (PARAM_ID, original, after_value, delta, threshold)
        )
    finally:
        # Restore + verify.
        osc.send_message(
            "/live/device/set/parameter/value",
            [TRACK_ID, DEVICE_ID, PARAM_ID, original],
        )
        wait_one_tick()
        restored = osc.query(
            "/live/device/get/parameter/value",
            [TRACK_ID, DEVICE_ID, PARAM_ID],
        )
        assert len(restored) == 4
        assert float(restored[3]) == pytest.approx(original), (
            "parameter restore failed — expected %r, got %r"
            % (original, restored)
        )
