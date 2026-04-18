"""Integration tests for the guarded-variant failure path.

The happy paths for scene tempo, time-signature, and song key/scale
are already covered by test_integration_scene.py and
test_integration_song_scale.py — those writes now ride
_set_property_guarded, and the existing tests confirm the wire shape
hasn't changed on success (guarded success path returns None, same as
the unwrapped generic path).

These tests exercise the FAILURE path: force the underlying setter to
raise, then assert the Bridge replies with the guarded error tuple
instead of timing out in silence. Without the guarded variants, these
same OSC calls would log the exception in Ableton's Log.txt and never
respond — the client would sit on a 5-second timeout.

Strategy: drive setattr with a bad value. We send missing / wrong-type
params to each guarded endpoint. setattr(scene, 'tempo', <bad>) raises
inside Live's native property setter, which the guarded variant
catches and turns into ("error: <ClassName>: <message>",). The scene /
song callback wrappers prepend the scene_index / song is stateless,
so the wire-level reply arrives as either (scene_index, "error: ...")
for scene or ("error: ...",) for song.

Teardown: each test reads the property before the mutation attempt
and re-reads after, asserting the value is unchanged (the failure path
must not partial-write).
"""
import pytest

from tests.integration.conftest import wait_one_tick

pytestmark = pytest.mark.integration


def _find_error_string(reply_tuple):
    """Return the first element in `reply_tuple` that begins with
    'error: ', or None if no such element is present. The Bridge's
    scene/song callback wrappers may prepend indices before the error
    string, so we can't assume the error sits at a fixed position."""
    for element in reply_tuple:
        if isinstance(element, str) and element.startswith("error: "):
            return element
    return None


# --------------------------------------------------------------------------
# Scene — undocumented properties (tempo, time_signature_*)
# --------------------------------------------------------------------------
SCENE_ID = 0


def test_guarded_scene_tempo_set_with_missing_value_returns_error_reply(osc):
    """Send /live/scene/set/tempo with only the scene index — no new
    value. The guarded setter tries to read params[0] and raises
    IndexError. We must get an OSC reply containing an 'error: ...'
    string within the default timeout, not a 5-second BridgeNotResponding."""
    before = osc.query("/live/scene/get/tempo", [SCENE_ID])
    assert len(before) >= 2, "scene tempo GET returned too few elements: %r" % (before,)
    original_tempo = float(before[1])

    try:
        reply = osc.query("/live/scene/set/tempo", [SCENE_ID])
    except Exception as e:
        pytest.fail(
            "guarded /live/scene/set/tempo with no value should have "
            "replied with an error tuple, not timed out. Got: %s" % (e,)
        )

    err = _find_error_string(reply)
    assert err is not None, (
        "expected an 'error: ...' string in the reply tuple — got %r" % (reply,)
    )
    # The wrapper prepends scene_index, so reply is (scene_index, error_str).
    assert reply[0] == SCENE_ID, (
        "scene_index was not prepended as expected — reply was %r" % (reply,)
    )

    # Verify the tempo wasn't partially written.
    after = osc.query("/live/scene/get/tempo", [SCENE_ID])
    assert float(after[1]) == pytest.approx(original_tempo), (
        "guarded failure path should not mutate the target — tempo changed "
        "from %r to %r" % (original_tempo, after[1])
    )


def test_guarded_scene_tempo_set_with_wrong_type_returns_error_reply(osc):
    """Send a string value to /live/scene/set/tempo. Live's native
    scene.tempo setter rejects non-numeric types. The guarded setter
    catches the resulting RuntimeError / TypeError and replies with an
    error tuple instead of letting the Remote Script log silently and
    Ohmic time out."""
    before = osc.query("/live/scene/get/tempo", [SCENE_ID])
    original_tempo = float(before[1])

    try:
        reply = osc.query(
            "/live/scene/set/tempo", [SCENE_ID, "not_a_tempo"],
        )
    except Exception as e:
        pytest.fail(
            "guarded /live/scene/set/tempo with bad type should have "
            "replied with an error tuple, not timed out. Got: %s" % (e,)
        )

    err = _find_error_string(reply)
    assert err is not None, (
        "expected an 'error: ...' string in the reply tuple — got %r" % (reply,)
    )
    assert reply[0] == SCENE_ID

    wait_one_tick()
    after = osc.query("/live/scene/get/tempo", [SCENE_ID])
    assert float(after[1]) == pytest.approx(original_tempo)


def test_guarded_scene_time_signature_numerator_bad_type_returns_error_reply(osc):
    """Same shape as the tempo test but for time_signature_numerator —
    the second of the five undocumented scene properties routed through
    the guarded path."""
    before = osc.query("/live/scene/get/time_signature_numerator", [SCENE_ID])
    original = int(before[1])

    reply = osc.query(
        "/live/scene/set/time_signature_numerator", [SCENE_ID, "xyz"],
    )
    err = _find_error_string(reply)
    assert err is not None, (
        "expected an 'error: ...' string in the reply tuple — got %r" % (reply,)
    )

    wait_one_tick()
    after = osc.query("/live/scene/get/time_signature_numerator", [SCENE_ID])
    assert int(after[1]) == original


# --------------------------------------------------------------------------
# Song — undocumented properties (root_note, scale_name)
# --------------------------------------------------------------------------
def test_guarded_song_root_note_bad_type_returns_error_reply(osc):
    """Live's song.root_note is an int (0–11). Sending a string should
    trigger the native setter's type check. The guarded setter catches
    it and replies with the error tuple. Song setters are stateless —
    the wrapper does not prepend anything, so the reply is directly
    ('error: ...',)."""
    before = osc.query("/live/song/get/root_note", [])
    # Wire: (value,) for song GETs (no index prefix).
    original = int(before[0])

    reply = osc.query("/live/song/set/root_note", ["not_a_pitch_class"])
    err = _find_error_string(reply)
    assert err is not None, (
        "expected an 'error: ...' string in the reply — got %r" % (reply,)
    )

    wait_one_tick()
    after = osc.query("/live/song/get/root_note", [])
    assert int(after[0]) == original


def test_guarded_song_scale_name_set_with_missing_value_returns_error_reply(osc):
    """Omit the value entirely. _set_property_guarded will try to read
    params[0] and raise IndexError. Error tuple must come back."""
    before = osc.query("/live/song/get/scale_name", [])
    original = str(before[0])

    reply = osc.query("/live/song/set/scale_name", [])
    err = _find_error_string(reply)
    assert err is not None, (
        "expected an 'error: ...' string in the reply — got %r" % (reply,)
    )

    wait_one_tick()
    after = osc.query("/live/song/get/scale_name", [])
    assert str(after[0]) == original
