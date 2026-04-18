"""Unit tests for the Bridge's guarded-variant generic callbacks.

The guarded variants live on AbletonOSCHandler in abletonosc/handler.py
and are the preferred setter/listener path for undocumented Live Object
Model properties (e.g. scene.tempo, song.root_note). They mirror the
@guarded_lom decorator pattern: catch any exception, log a full
manually-formatted traceback, and return an OSC error tuple of the
shape ("error: <ClassName>: <message>",).

These tests exercise the three guarded variants (_set_property_guarded,
_start_listen_guarded, _stop_listen_guarded) via an AbletonOSCHandler
instance whose Live imports have been stubbed, so the module loads
without pulling in Ableton's embedded Python environment. The shape of
the stubs matches test_handler_unit.py.
"""
import logging
import sys
import types

import pytest


def _load_handler_module():
    """Import abletonosc.handler without triggering Ableton's Live
    imports. Mirrors the pattern in test_handler_unit.py so the two
    test files can coexist in the same pytest run without fighting
    over sys.modules entries."""
    if "ableton" not in sys.modules:
        ableton = types.ModuleType("ableton")
        ableton.v2 = types.ModuleType("ableton.v2")
        ableton.v2.control_surface = types.ModuleType("ableton.v2.control_surface")
        ableton.v2.control_surface.component = types.ModuleType(
            "ableton.v2.control_surface.component"
        )
        ableton.v2.control_surface.component.Component = type("Component", (), {})
        sys.modules["ableton"] = ableton
        sys.modules["ableton.v2"] = ableton.v2
        sys.modules["ableton.v2.control_surface"] = ableton.v2.control_surface
        sys.modules["ableton.v2.control_surface.component"] = (
            ableton.v2.control_surface.component
        )
    if "abletonosc.osc_server" not in sys.modules:
        osc_server_stub = types.ModuleType("abletonosc.osc_server")
        osc_server_stub.OSCServer = type("OSCServer", (), {})
        sys.modules["abletonosc.osc_server"] = osc_server_stub

    import importlib
    from abletonosc import handler as handler_module
    importlib.reload(handler_module)
    return handler_module


def _make_handler_instance(handler_module):
    """Build an AbletonOSCHandler-like instance without running
    __init__ (which requires a manager with an osc_server). We need
    only the attributes the guarded methods touch: logger,
    class_identifier, listener_functions, listener_objects, osc_server.
    """
    inst = handler_module.AbletonOSCHandler.__new__(
        handler_module.AbletonOSCHandler
    )
    inst.logger = logging.getLogger("abletonosc")
    inst.class_identifier = "probe"
    inst.listener_functions = {}
    inst.listener_objects = {}

    class _FakeOSCServer:
        def __init__(self):
            self.sent = []

        def send(self, address, params):
            self.sent.append((address, params))

    inst.osc_server = _FakeOSCServer()
    return inst


# ---------------------------------------------------------------------------
# _set_property_guarded
# ---------------------------------------------------------------------------
def test_set_property_guarded_success_returns_none():
    handler = _load_handler_module()
    inst = _make_handler_instance(handler)

    class Target:
        pass
    t = Target()

    result = inst._set_property_guarded(t, "tempo", (128.5,))

    assert result is None
    assert t.tempo == 128.5


def test_set_property_guarded_attribute_error_returns_error_tuple(caplog):
    """Simulate Ableton removing an undocumented property: setattr raises
    AttributeError because the Live stub actively rejects the write."""
    handler = _load_handler_module()
    inst = _make_handler_instance(handler)

    class Target:
        def __setattr__(self, name, value):
            raise AttributeError("tempo was removed in this Live version")

    with caplog.at_level(logging.ERROR, logger="abletonosc"):
        result = inst._set_property_guarded(Target(), "tempo", (128.5,))

    assert isinstance(result, tuple)
    assert len(result) == 1
    assert result[0].startswith("error: ")
    assert "AttributeError" in result[0]
    assert "tempo was removed" in result[0]

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    combined = "\n".join(r.getMessage() for r in error_records)
    assert "probe set tempo failed" in combined, (
        "expected the class_identifier + prop + 'failed' in the log"
    )
    assert "AttributeError" in combined
    assert "Traceback" in combined, (
        "expected a manually-formatted traceback in the error log"
    )


def test_set_property_guarded_runtime_error_returns_error_tuple():
    """Some Live properties raise RuntimeError on invalid values rather
    than AttributeError (e.g. out-of-range writes). Guard must catch
    those too."""
    handler = _load_handler_module()
    inst = _make_handler_instance(handler)

    class Target:
        def __setattr__(self, name, value):
            raise RuntimeError("value out of range")

    result = inst._set_property_guarded(Target(), "scale_name", ("Lydian",))

    assert result[0].startswith("error: ")
    assert "RuntimeError" in result[0]
    assert "value out of range" in result[0]


# ---------------------------------------------------------------------------
# _start_listen_guarded
# ---------------------------------------------------------------------------
def test_start_listen_guarded_success_registers_and_fires_initial_value():
    handler = _load_handler_module()
    inst = _make_handler_instance(handler)

    class Target:
        def __init__(self):
            self.tempo = 120.0
            self._listeners = []

        def add_tempo_listener(self, cb):
            self._listeners.append(cb)

        def remove_tempo_listener(self, cb):
            self._listeners.remove(cb)

    t = Target()
    result = inst._start_listen_guarded(t, "tempo", ())

    assert result is None
    # Listener was registered on the target.
    assert len(t._listeners) == 1
    # And the initial-value callback fired exactly once.
    assert len(inst.osc_server.sent) == 1
    address, params = inst.osc_server.sent[0]
    assert address == "/live/probe/get/tempo"
    assert params == (120.0,)


def test_start_listen_guarded_missing_add_listener_returns_error_tuple(caplog):
    """Simulate Ableton removing `add_tempo_listener`: getattr in
    _start_listen_guarded raises AttributeError. The guard must swallow
    it and return an error tuple so Ohmic gets a clean reply instead of
    timing out."""
    handler = _load_handler_module()
    inst = _make_handler_instance(handler)

    class Target:
        # Intentionally no add_tempo_listener.
        tempo = 120.0

    with caplog.at_level(logging.ERROR, logger="abletonosc"):
        result = inst._start_listen_guarded(Target(), "tempo", ())

    assert isinstance(result, tuple)
    assert len(result) == 1
    assert result[0].startswith("error: ")
    assert "AttributeError" in result[0]

    combined = "\n".join(
        r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
    )
    assert "probe start_listen tempo failed" in combined
    assert "Traceback" in combined


def test_start_listen_guarded_inner_listener_errors_are_swallowed():
    """The inner property_changed_callback may fire long after
    _start_listen_guarded returned — e.g. when Live updates the scene
    tempo. If the callback raises (because the property was removed
    after the listener was installed), it must not poison Live's
    listener machinery: swallow and log."""
    handler = _load_handler_module()
    inst = _make_handler_instance(handler)

    class Target:
        def __init__(self):
            self._listeners = []
            self._calls = 0

        @property
        def tempo(self):
            # First call (during start_listen's initial fire) works.
            # Second call (simulating a later Live-triggered fire) fails.
            self._calls += 1
            if self._calls == 1:
                return 120.0
            raise AttributeError("tempo removed mid-session")

        def add_tempo_listener(self, cb):
            self._listeners.append(cb)

        def remove_tempo_listener(self, cb):
            self._listeners.remove(cb)

    t = Target()
    result = inst._start_listen_guarded(t, "tempo", ())
    assert result is None  # initial fire succeeded

    # Now simulate Live invoking the listener again — this must not
    # escape the callback.
    cb = t._listeners[0]
    cb()  # should not raise


# ---------------------------------------------------------------------------
# _stop_listen_guarded
# ---------------------------------------------------------------------------
def test_stop_listen_guarded_success_returns_none():
    handler = _load_handler_module()
    inst = _make_handler_instance(handler)

    class Target:
        def __init__(self):
            self._listeners = []

        def add_tempo_listener(self, cb):
            self._listeners.append(cb)

        def remove_tempo_listener(self, cb):
            self._listeners.remove(cb)

    t = Target()
    inst._start_listen_guarded(t, "tempo", ())
    assert len(t._listeners) == 1

    result = inst._stop_listen_guarded(t, "tempo", ())
    assert result is None
    assert len(t._listeners) == 0
    # And the listener bookkeeping was cleaned.
    assert (("tempo", ())) not in inst.listener_functions


def test_stop_listen_guarded_missing_remove_listener_returns_error_tuple(caplog):
    """Simulate Ableton removing `remove_tempo_listener` between
    registration and teardown: the outer getattr raises AttributeError.
    Guard surfaces it as an OSC error reply."""
    handler = _load_handler_module()
    inst = _make_handler_instance(handler)

    # Manually install a bookkeeping entry so _stop_listen_guarded
    # proceeds past the "not registered" branch.
    class Target:
        # No remove_tempo_listener attribute.
        pass
    t = Target()
    inst.listener_functions[("tempo", ())] = lambda: None
    inst.listener_objects[("tempo", ())] = t

    with caplog.at_level(logging.ERROR, logger="abletonosc"):
        result = inst._stop_listen_guarded(t, "tempo", ())

    assert isinstance(result, tuple)
    assert result[0].startswith("error: ")
    assert "AttributeError" in result[0]

    combined = "\n".join(
        r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
    )
    assert "probe stop_listen tempo failed" in combined


def test_stop_listen_guarded_remove_listener_raising_is_benign():
    """If the listener has already been disconnected (e.g. target object
    was destroyed), remove_listener may raise. The inner try/except
    existed in _stop_listen for the same reason; _stop_listen_guarded
    preserves that benign-exception behavior and still returns None."""
    handler = _load_handler_module()
    inst = _make_handler_instance(handler)

    class Target:
        def remove_tempo_listener(self, cb):
            raise RuntimeError("observer no longer connected")

    t = Target()
    inst.listener_functions[("tempo", ())] = lambda: None
    inst.listener_objects[("tempo", ())] = t

    result = inst._stop_listen_guarded(t, "tempo", ())
    # Benign — treated as success, bookkeeping cleared, no error tuple.
    assert result is None
    assert (("tempo", ())) not in inst.listener_functions
