"""Root pytest configuration for Ohmic-Bridge.

The hyphen in the repo folder name ('Ohmic-Bridge') means the folder
is not importable as a Python package, which breaks the relative
import `from ..client import ...` at the top of tests/__init__.py
during pytest collection. That import was inherited from the upstream
AbletonOSC fork and is only useful when integration tests are driven
from a shell with Ableton already running.

To let pure unit tests run under pytest without rewriting the legacy
integration-test scaffolding, we pre-register an empty `tests`
package in sys.modules here, before pytest tries to import the real
tests/__init__.py. The real module still exists on disk; integration
test runs invoked with Ableton running will find its AbletonOSCClient
helper via sys.path manipulation in the test files that actually
need it.
"""
import os
import sys
import types

_bridge_root = os.path.dirname(os.path.abspath(__file__))
if _bridge_root not in sys.path:
    sys.path.insert(0, _bridge_root)

collect_ignore = [
    os.path.join("tests", name)
    for name in (
        "test_application.py",
        "test_bundle.py",
        "test_clip.py",
        "test_clip_slot.py",
        "test_song.py",
        "test_track.py",
        "test_view.py",
    )
]

# Pre-register an empty `tests` package so pytest does not execute
# the real tests/__init__.py during collection. Unit tests do not
# depend on its contents.
if "tests" not in sys.modules:
    _stub = types.ModuleType("tests")
    _stub.__path__ = [os.path.join(_bridge_root, "tests")]
    sys.modules["tests"] = _stub

# Pre-register an empty `abletonosc` package so unit tests can import
# individual submodules (e.g. `abletonosc.capabilities`) without
# triggering the eager handler wiring in the real
# abletonosc/__init__.py, which pulls in modules that assume
# Ableton's runtime is present (pythonosc relative imports, handler
# registrations, etc.). Integration tests that need the full package
# run inside Ableton where those imports resolve naturally.
if "abletonosc" not in sys.modules:
    _stub = types.ModuleType("abletonosc")
    _stub.__path__ = [os.path.join(_bridge_root, "abletonosc")]
    sys.modules["abletonosc"] = _stub


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_abletonosc_submodule_attrs():
    """After each test, clear any submodule attributes that were
    cached on the `abletonosc` stub package. Unit tests reset the
    sys.modules entries in fixture teardown, but Python also sets
    each submodule as an attribute on its parent package when
    imported — and that attribute survives sys.modules.pop(), which
    breaks importlib.reload() on the next test."""
    yield
    _stub = sys.modules.get("abletonosc")
    if _stub is None:
        return
    for attr in list(vars(_stub)):
        if attr.startswith("_"):
            continue
        sub = getattr(_stub, attr, None)
        if isinstance(sub, types.ModuleType):
            delattr(_stub, attr)
