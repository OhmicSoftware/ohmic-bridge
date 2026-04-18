"""Unit tests for the Bridge's handler-level exception wrappers.

The decorators live in abletonosc/handler.py. These tests verify
that a callback wrapped by @guarded_lom catches any exception, logs
it with a full traceback via the module logger, and returns an
OSC-ready tuple of the shape ("error: <ClassName>: <message>",) —
never propagating the exception up to the OSC server's dispatch
loop (which would otherwise crash the Remote Script).
"""
import logging
import sys
import types

import pytest


def _load_handler_module():
    """Import abletonosc.handler without triggering Ableton's Live
    imports. The module imports `ableton.v2.control_surface.component`
    at the top; stub that first."""
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


def test_guarded_lom_passes_through_successful_return():
    handler = _load_handler_module()

    @handler.guarded_lom("noop")
    def ok_handler(clip, params=()):
        return (1, 2, 3)

    assert ok_handler(object(), ()) == (1, 2, 3)


def test_guarded_lom_catches_exception_and_returns_error_tuple():
    handler = _load_handler_module()

    @handler.guarded_lom("boom")
    def bad_handler(clip, params=()):
        raise ValueError("thing went wrong")

    result = bad_handler(object(), ())
    assert isinstance(result, tuple)
    assert len(result) == 1
    assert result[0].startswith("error: ")
    assert "ValueError" in result[0]
    assert "thing went wrong" in result[0]


def test_guarded_lom_logs_full_traceback(caplog):
    handler = _load_handler_module()

    @handler.guarded_lom("log_me")
    def raises(*a, **kw):
        raise RuntimeError("observe me")

    with caplog.at_level(logging.ERROR, logger="abletonosc"):
        raises()

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("log_me" in r.getMessage() for r in error_records), (
        "expected at least one ERROR-level log record mentioning the handler name"
    )
    assert any(r.exc_info is not None for r in error_records), (
        "expected at least one record to carry exc_info (full traceback)"
    )


def test_guarded_lom_json_passes_through_successful_return():
    handler = _load_handler_module()

    @handler.guarded_lom_json("ok_json")
    def ok_handler(_):
        import json
        return (json.dumps({"ok": True}),)

    import json
    result = ok_handler(())
    assert len(result) == 1
    parsed = json.loads(result[0])
    assert parsed == {"ok": True}


def test_guarded_lom_json_returns_error_dict_on_exception():
    handler = _load_handler_module()

    @handler.guarded_lom_json("bad_json")
    def bad_handler(_):
        raise KeyError("missing")

    import json
    result = bad_handler(())
    assert len(result) == 1
    parsed = json.loads(result[0])
    assert "error" in parsed
    assert "KeyError" in parsed["error"]
    assert "missing" in parsed["error"]
    assert parsed["handler"] == "bad_json"
