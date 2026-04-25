"""Guards for Bridge integration tests owning their Ableton state."""

from pathlib import Path


_ROOT = Path(__file__).resolve().parent / "integration"


def test_clip_integration_tests_do_not_target_project_track_zero():
    brittle_files = [
        _ROOT / "test_integration_clip_properties.py",
        _ROOT / "test_integration_clip_slot.py",
        _ROOT / "test_integration_clip_slot_duplicate.py",
        _ROOT / "test_integration_view.py",
    ]

    for path in brittle_files:
        src = path.read_text(encoding="utf-8")
        assert "TRACK_ID = 0" not in src, (
            f"{path.name} must create and target a disposable MIDI track "
            "instead of assuming project track 0 can host MIDI clips"
        )


def test_track_property_tests_do_not_require_project_track_zero_midi():
    src = (_ROOT / "test_integration_track.py").read_text(encoding="utf-8")

    assert "_require_midi_track_0" not in src
    assert "TRACK_ID = 0" not in src

