# Integration Testing

Pre-release gate for the Ohmic Bridge. Run these tests against a real Ableton Live install + Ohmic_Bridge Remote Script before every release that touches a capability-gated handler. The goal is to catch Ableton LOM drift between Live versions before users hit it.

## What they cover

One happy-path round-trip per capability bucket (11 buckets, 9 test files). Each test creates a known shape in Ableton, performs an operation via the OSC endpoint, reads the result back, and asserts every field matches the expected value byte-for-byte. Any drift in an Ableton API (signature change, return shape change, new required argument) will fail one of these tests long before a user's session silently breaks.

## Prerequisites

- Ableton Live 11 or newer running.
- Ohmic_Bridge installed and enabled as a Control Surface (Settings -> Link, Tempo & MIDI -> Ohmic_Bridge on a Control Surface slot with Input and Output both set to None).
- A default project open on Ableton's side. Specifically: a MIDI track at index 0, a scene at index 0.
- Python 3.13 with `pytest` and `python-osc` installed.
- OSC ports 11002 and 11003 not in use by other processes.

## Running

From the Bridge repo root:

```bash
# Default pytest - unit tests only; integration tests are skipped.
python -m pytest

# Integration tests only - requires Ableton running with Bridge loaded.
python -m pytest -m integration

# One specific integration test file.
python -m pytest -m integration tests/integration/test_integration_clip_notes.py

# Both unit and integration.
python -m pytest -m "integration or not integration"
```

## When a test fails

Two likely causes:

1. **Ableton API change.** The most common reason after a new Live release. Check Ableton's release notes and the relevant endpoint in `abletonosc/*.py`. If a method was removed or renamed, update the Bridge handler accordingly, update `BRIDGE_LOM_AUDIT.md`, and add a CHANGELOG entry.

2. **Project state mismatch.** Tests assume a default project (first MIDI track at index 0, first scene at index 0, etc.). If the open project doesn't meet those preconditions, tests will fail for the wrong reason. Open a fresh default project and try again.

## Adding a new integration test

1. Identify which capability bucket the new handler belongs to (or add a new bucket in `BRIDGE_LOM_AUDIT.md` if genuinely new undocumented surface).
2. Add a test function to the bucket's file in `tests/integration/`. Follow the arrange/act/assert/teardown shape.
3. Ensure the test cleans up after itself so repeat runs stay idempotent.
4. Run locally; confirm GREEN.
5. Commit.

## Failure modes the suite does NOT cover

- **Error paths** - what happens when you pass invalid args. Useful to add later.
- **Performance / stress** - these tests are single-message round-trips.
- **Cross-version matrix** - a single run targets whatever Live is open. For version coverage, run the suite against each Live version manually before a release.
