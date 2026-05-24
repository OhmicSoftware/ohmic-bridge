# Integration Testing

Pre-release gate for the Ohmic Bridge. Run these tests against a real Ableton Live install + Ohmic_Bridge Remote Script before every release that touches a capability-gated handler or an OSC handler of any kind. The goal is to catch Ableton LOM drift between Live versions — and wire-format regressions in the Bridge itself — before users hit them.

## What they cover

**Focused tests cover every OSC endpoint Ohmic's MCP server sends to the Bridge.** Each test creates a known shape in Ableton, performs an operation via the OSC endpoint, reads the result back, and asserts the endpoint-specific wire fields and resulting Live state. Any drift in an Ableton API (signature change, return shape change, new required argument) or in the Bridge's wire contract will fail at least one test long before a user's session silently breaks.

**Every test owns its state.** Tests that need an empty MIDI track create one via `/live/song/create_midi_track` and delete it on teardown. Tests that need a clip at a specific slot create it via `/live/clip_slot/create_clip`. Tests that need a device to operate on load it first. This means the suite runs green against any Live project — there are no project-state skips.

## Core rule — every write is verified by a read

**Every OSC write in every integration test MUST be immediately followed by an OSC read that verifies the write landed.** No fire-and-forget. No batch-write-then-verify-at-end. Each `send_message(...)` is paired with a `query(...)` and a byte-for-byte assertion before the next action.

**Why:** OSC is asynchronous fire-and-forget at the wire level. A `send_message` that looks successful on the Python side (no exception, socket accepts the datagram) can fail silently at the Bridge — handler raised, arg types mismatched, previous test left state that made the call a no-op, or Ableton's LOM silently ignored the request. The only way to prove a write took effect is to read the state back. Batch-style tests (`add_note_1`, `add_note_2`, `add_note_3`, then query once) are unsafe: if one of the adds failed, the count-only assertion still passes.

Teardown fixtures that restore state follow the same rule — write the restore, then read back to verify the restore landed. A failed restore that leaves Ableton in a dirty state is as bad as a failed mutation because it pollutes the next test.

If an endpoint is a pure setter with no matching getter (direct or indirect), it is fundamentally untestable in integration — mark the test `@pytest.mark.skip(reason="no observable getter")` rather than fake it. A green test without a read is worse than a skip because it gives false confidence.

## Prerequisites

- Ableton Live 11 or newer running (Live 12 preferred — newer capability buckets gate on Live 12 features).
- Ohmic_Bridge installed and enabled as a Control Surface (Settings → Link, Tempo & MIDI → Ohmic_Bridge on a Control Surface slot with Input and Output both set to None).
- A Live project with at least one MIDI track at index 0 and at least one scene at index 0 (any default project meets this). Tests that need additional state (empty tracks, clips, devices) create it themselves and tear down after.
- Python 3.13 with `pytest` and `python-osc` installed.
- OSC ports 11002 and 11003 not in use by other processes.

## Running

From the Bridge repo root:

```bash
# Default pytest — unit tests only; integration tests are skipped.
python -m pytest

# Integration tests only — requires Ableton running with Bridge loaded.
python -m pytest -m integration

# One specific integration test file.
python -m pytest -m integration tests/integration/test_integration_clip_notes.py

# Browser metadata lifecycle and file-type matrix.
python -m pytest -m integration tests/integration/test_integration_browser_metadata.py -v

# Both unit and integration.
python -m pytest -m "integration or not integration"
```

**Do NOT parallelize.** All integration tests target the same Ableton process and many share `TRACK_ID = 0, CLIP_ID = 0`. Running two tests concurrently on the same slot will thrash. Every integration test file has a "do not parallelize" header comment and pytest-xdist is never enabled in our invocation guidance.

## How the opt-in gating works

The Bridge's `pyproject.toml` declares `addopts = "-m 'not integration'"`. Every integration test file has a module-level `pytestmark = pytest.mark.integration`. When pytest runs without an explicit `-m` argument, every integration test is deselected by the default filter. Only `pytest -m integration` (or the explicit override `pytest -m "integration or not integration"`) runs them.

The one known bypass is `pytest -o addopts=""` — an explicit override of the default marker filter. That's deliberate developer ergonomics, not a loophole.

## File layout

```
tests/integration/
├── __init__.py
├── conftest.py                           # osc fixture + _quantization_none + wait_one_tick
├── test_integration_arrangement.py       # arrangement_clips bucket
├── test_integration_browser.py           # browser bucket
├── test_integration_browser_metadata.py  # file-backed browser metadata lifecycle
├── test_integration_clip_notes.py        # clip_notes_rw bucket (session)
├── test_integration_clip_properties.py   # clip name/color/length/looping
├── test_integration_clip_slot.py         # clip_slot has_clip/fire/stop
├── test_integration_clip_slot_duplicate.py # clip_slot_duplicate bucket
├── test_integration_cue_points.py        # song_cue_points bucket
├── test_integration_device.py            # device params + value_string
├── test_integration_infrastructure.py    # heartbeat, bridge_version, capabilities, log_level, app version
├── test_integration_scene.py             # scene tempo/TS/fire/name
├── test_integration_session_info.py      # JSON-returning aggregate handlers
├── test_integration_song_scale.py        # song_scale_properties bucket
├── test_integration_song_transport.py    # transport, tempo, num_scenes, track_names, create tracks/scenes
├── test_integration_track.py             # track name/mute/devices/delete_device/move_device
├── test_integration_view.py              # view selected_clip roundtrip
└── browser_metadata_fixtures.py          # User Library fixture discovery/copy/cleanup helpers
```

## Transport-state caveat

Ableton's transport state (`/live/song/get/is_playing`) does not flip immediately on `start_playing` / `stop_playing` when `clip_trigger_quantization` is anything other than `"none"`. The session default is usually `"1 bar"` which means a transport change waits up to one bar to take effect, causing transport tests to flake.

Tests that depend on transport state use the shared `_quantization_none` fixture in `conftest.py`: set quantization to `"none"` at test start (verified via read-back), run the test, restore the original value on teardown (also verified). If you add a new test that exercises transport state, depend on this fixture by argument name.

## Browser metadata fixture folder

`tests/integration/test_integration_browser_metadata.py` owns exactly one temporary folder in the Ableton User Library: `_OhmicMetadataE2E`. The helper creates `_OhmicMetadataE2E/.ohmic_metadata_e2e.json` before copying fixtures. Cleanup refuses to delete `_OhmicMetadataE2E` unless that marker file is present, so a manually-created or suspicious folder with the same name fails loudly instead of being removed.

The helper removes a stale marked `_OhmicMetadataE2E` folder at test start, copies fresh source files into category-shaped User Library subfolders, and registers pytest finalizers so filesystem cleanup still runs after failed tests. After explicit cleanup, the metadata tests poll `/live/browser/get/metadata_page` until the owned browser paths are no longer active; `stale_missing_file` entries are treated as stale cache diagnostics, not active files.

Source discovery scans only the Ableton User Library root (`E:\Ableton\User Library` on Adam's Windows PC, `/Users/awilki01/Music/Ableton/User Library` on Adam's macOS laptop), prefers small files, and excludes `Remote Scripts` plus `_OhmicMetadataE2E`. The tests never create backups or sibling copies under `Remote Scripts`; do not add Remote Scripts backup behavior to this fixture.

Do not parallelize these tests. In addition to the suite-wide Ableton process constraint, the helper skips clearly when `PYTEST_XDIST_WORKER` is present and also acquires `_OhmicMetadataE2E.lock` with exclusive creation before touching `_OhmicMetadataE2E`.

## When a test fails

Three likely causes, in order of probability:

1. **Ableton API change.** The most common reason after a new Live release. Check Ableton's release notes and the relevant handler in `abletonosc/*.py`. If a method was removed or renamed, update the Bridge handler, update `BRIDGE_LOM_AUDIT.md`, and add a CHANGELOG entry.

2. **Bridge wire-format regression.** If a handler was edited recently, a round-trip test might fail because the Bridge is now sending back a different shape than it used to. Read the failing test's assertion against the actual wire output — the assertion is the spec.

3. **Project state precondition not met.** Tests assume a default project (first MIDI track at index 0, first scene at index 0, empty MIDI track somewhere for browser-load). If the open project doesn't meet those preconditions, the affected tests skip with a documented reason rather than fail — see the skip messages. Open a fresh default project and re-run to eliminate skips.

## Adding a new integration test

1. Identify which OSC endpoint the test covers. If the endpoint is new in the Bridge, add its capability bucket to `BRIDGE_LOM_AUDIT.md` first.
2. Add the test to the appropriate file in `tests/integration/` (or create a new file matching the Bridge's file structure).
3. Declare `pytestmark = pytest.mark.integration` at module level and include a "do not parallelize" header comment.
4. Follow the arrange / act / assert / teardown shape. **Every `send_message` must be followed by a `query` + assertion before the next action.**
5. Teardown: restore any mutated state and verify the restore with a read-back.
6. Run locally against a real Ableton; confirm the new test passes and existing tests don't regress.
7. Commit.

## Skips

The suite currently has **zero project-state skips**. Every test that needs specific project state (empty MIDI track, a clip at a specific slot, a loaded device) creates that state via OSC calls in its fixture or test body and tears down after. The only remaining skip condition is environment-level: if Ableton isn't reachable, the `osc` session fixture calls `pytest.exit(...)` and the whole suite bails with a clear "start Ableton + Ohmic_Bridge" message — documented in `conftest.py`.

If a contributor adds a new test that skips for project-state reasons, treat it as a test-design defect — make the test create the state it needs, not document what's missing.

## Failure modes the suite does NOT cover

- **Error paths** — what happens when you pass invalid args. Covered by unit tests for the `@guarded_lom` decorators; not replicated at the integration level except for a handful of malformed-args probes.
- **Performance / stress** — these tests are single-message round-trips.
- **Cross-version matrix** — a single run targets whatever Live version is open. For version coverage, run the suite against each Live version manually before a release and record the result in the history table below.
- **LLM behavior** — prompt parsing, tool-call selection, response interpretation. Different test category.

## Runtime history

Record wall-clock time after each pass so growth is visible. Fail the run in review if runtime exceeds 2× the current budget.

| Date | Pass | Tests | Runtime |
|---|---|---|---|
| 2026-04-18 | initial 16 | 16 passed | 4s |
| 2026-04-18 | +Pass 1 (capability/infra) | 24 passed, 1 skipped | 10.6s |
| 2026-04-18 | +Pass 2 (transport/track) | 39 passed, 5 skipped | 19.6s |
| 2026-04-18 | +Pass 3 (clip/slot/scene/view/device) | 53 passed, 6 skipped | 34.2s |
| 2026-04-18 | +rewrite 5 skip-prone tests to own state | 58 passed, 0 skipped | 40.6s |
| 2026-04-25 | destructive ack + Bridge 0.4.0 focused verification | 17 passed, 2 skipped | 21.65s |
