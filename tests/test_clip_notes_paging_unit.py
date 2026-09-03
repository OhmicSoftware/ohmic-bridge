"""Unit tests for the paged /get/notes replies introduced in Bridge 0.5.2.

Both note-read endpoints answer in pages of at most MAX_NOTES_PER_PAGE
notes so no reply exceeds the 9,216-byte UDP datagram macOS allows by
default. Uses the fake-handler harness of test_destructive_ack_unit."""

import types

import pytest
from pythonosc.osc_message_builder import OscMessageBuilder

from tests.test_destructive_ack_unit import _fresh_clip_module

SESSION = "/live/clip/get/notes"
ARRANGEMENT = "/live/arrangement_clip/get/notes"
MAC_UDP_DATAGRAM_CAP = 9216


class _Note:
    def __init__(self, pitch, start_time, duration=0.25, velocity=100,
                 mute=False, probability=1.0):
        self.pitch = pitch
        self.start_time = start_time
        self.duration = duration
        self.velocity = velocity
        self.mute = mute
        self.probability = probability

    def wire(self):
        return (self.pitch, self.start_time, self.duration,
                self.velocity, self.mute, self.probability)


class _Clip:
    def __init__(self, notes):
        self.notes = list(notes)
        self.queried_args = []

    def get_notes_extended(self, pitch_start, pitch_span, time_start, time_span):
        self.queried_args.append((pitch_start, pitch_span, time_start, time_span))
        return list(self.notes)


def _handler_for(notes):
    clip_module = _fresh_clip_module()
    handler = clip_module.ClipHandler.__new__(clip_module.ClipHandler)
    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    clip = _Clip(notes)
    track = types.SimpleNamespace(
        clip_slots=[types.SimpleNamespace(clip=clip)],
        arrangement_clips=[clip],
    )
    handler.osc_server = _Server()
    handler.song = types.SimpleNamespace(tracks=[track])
    handler._clip_notes_cache = []
    handler.init_api()
    return clip_module, callbacks, clip


def _flat(notes):
    return tuple(value for note in notes for value in note.wire())


def _many_notes(count):
    return [_Note(36 + index % 60, float(index) * 0.25) for index in range(count)]


@pytest.mark.parametrize("address", [SESSION, ARRANGEMENT])
def test_notes_past_one_page_split_into_exact_slices(address):
    clip_module, callbacks, clip = _handler_for(_many_notes(2 * 300 + 7))
    page_size = clip_module.MAX_NOTES_PER_PAGE
    assert page_size == 300

    pages = [callbacks[address]((0, 0, page)) for page in range(3)]

    for page_index, reply in enumerate(pages):
        assert reply[:5] == (0, 0, page_index, 3, 607)
        expected = _flat(clip.notes[page_index * page_size:(page_index + 1) * page_size])
        assert reply[5:] == expected
    assert sum((reply[5:] for reply in pages), ()) == _flat(clip.notes)


def test_zero_and_window_forms_answer_page_zero_with_the_request_echoed():
    _, callbacks, clip = _handler_for(_many_notes(2 * 300 + 7))

    full = callbacks[SESSION]((0, 0))
    assert full[:4] == (0, 0, 3, 607)
    assert full[4:] == _flat(clip.notes[:300])
    assert clip.queried_args[-1] == (0, 127, -8192, 16384)

    windowed = callbacks[SESSION]((0, 0, 60, 13, 1.0, 1.5))
    assert windowed[:8] == (0, 0, 60, 13, 1.0, 1.5, 3, 607)
    assert windowed[8:] == _flat(clip.notes[:300])
    assert clip.queried_args[-1] == (60, 13, 1.0, 1.5)

    last_page = callbacks[ARRANGEMENT]((0, 0, 60, 13, 1.0, 1.5, 2))
    assert last_page[:9] == (0, 0, 60, 13, 1.0, 1.5, 2, 3, 607)
    assert last_page[9:] == _flat(clip.notes[600:])


@pytest.mark.parametrize("params", [(0, 0, 3), (0, 0, -1), (0, 0, 60, 13)])
def test_bad_page_index_or_arg_count_is_a_guarded_error(params):
    _, callbacks, _ = _handler_for(_many_notes(5))

    reply = callbacks[SESSION](params)

    assert reply[:2] == (0, 0)
    assert reply[2].startswith("error: ValueError:")


def test_empty_clip_is_one_empty_page():
    _, callbacks, _ = _handler_for([])

    assert callbacks[ARRANGEMENT]((0, 0)) == (0, 0, 1, 0)
    assert callbacks[ARRANGEMENT]((0, 0, 0)) == (0, 0, 0, 1, 0)


def _encoded_reply_size(address, params):
    builder = OscMessageBuilder(address)
    for param in params:
        builder.add_arg(param)
    return len(builder.build().dgram)


def test_a_full_page_fits_the_shared_reply_budget():
    clip_module, callbacks, _ = _handler_for([
        _Note(127, 16383.75, 16383.75, 127, True, 1.0) for _ in range(300)
    ])
    assert clip_module.MAX_NOTES_PER_PAGE == 300
    # Longest possible echo: window plus page index, largest page numbers.
    reply = callbacks[ARRANGEMENT]((0, 0, 0, 128, -8192, 16384, 0))
    reply = reply[:7] + (2147483647, 2147483647) + reply[9:]

    size = _encoded_reply_size(ARRANGEMENT, reply)

    assert size <= clip_module.MAX_REPLY_BYTES == 8192
    assert size <= MAC_UDP_DATAGRAM_CAP
    # The premise this fix answers: 640 notes (20 x 32 bars) in one
    # datagram do not fit the Mac cap.
    single_datagram = (0, 0, 1, 640) + _flat(
        [_Note(60, float(index), 0.25) for index in range(640)]
    )
    assert _encoded_reply_size(ARRANGEMENT, single_datagram) > MAC_UDP_DATAGRAM_CAP
