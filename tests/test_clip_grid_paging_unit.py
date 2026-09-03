"""Unit tests for the paged /live/song/get/clip_grid reply.

clip_grid answers in pages so no reply exceeds the 9,216-byte UDP
datagram macOS allows by default. Uses the fresh-module harness of
test_destructive_ack_unit."""

import json
import logging
import types

from pythonosc.osc_message_builder import OscMessageBuilder

from tests.test_destructive_ack_unit import _fresh_song_module

MAC_UDP_DATAGRAM_CAP = 9216
ADDRESS = "/live/song/get/clip_grid"


class _Clip:
    def __init__(self, name, color):
        self.name = name
        self.color = color


class _ClipSlot:
    def __init__(self, clip=None):
        self.clip = clip


class _Track:
    def __init__(self, clip_slots):
        self.clip_slots = clip_slots


def _handler_for(tracks, num_scenes):
    song_module = _fresh_song_module()
    handler = song_module.SongHandler.__new__(song_module.SongHandler)
    callbacks = {}

    class _Server:
        def add_handler(self, address, callback):
            callbacks[address] = callback

    handler.osc_server = _Server()
    handler.logger = logging.getLogger("abletonosc")
    handler.song = types.SimpleNamespace(tracks=tracks, scenes=list(range(num_scenes)))
    handler.init_api()
    return song_module, callbacks


def test_dense_grid_splits_into_pages_that_fit_the_datagram_budget():
    entries_wanted = 233
    tracks = []
    filled = 0
    for track_idx in range(10):
        slots = []
        for slot_idx in range(30):
            if filled < entries_wanted:
                name = "Verse Groove Bassline Variation Take %03d" % filled
                color = ((filled * 37) % 256) << 16 | ((filled * 53) % 256) << 8 | (filled * 11) % 256
                slots.append(_ClipSlot(_Clip(name, color)))
                filled += 1
            else:
                slots.append(_ClipSlot(None))
        tracks.append(_Track(slots))

    _, callbacks = _handler_for(tracks, num_scenes=30)

    first_reply = callbacks[ADDRESS]((0,))
    page_count = json.loads(first_reply[1])["page_count"]
    assert page_count >= 2

    merged_clips = {}
    merged_colors = {}
    for page_index in range(page_count):
        reply = callbacks[ADDRESS]((page_index,))
        assert reply[0] == page_index
        builder = OscMessageBuilder(ADDRESS)
        for arg in reply:
            builder.add_arg(arg)
        assert len(builder.build().dgram) <= MAC_UDP_DATAGRAM_CAP
        page = json.loads(reply[1])
        assert page["page_index"] == page_index
        assert page["page_count"] == page_count
        merged_clips.update(page["clips"])
        merged_colors.update(page["clip_colors"])

    assert len(merged_clips) == entries_wanted
    assert len(merged_colors) == entries_wanted


def test_empty_grid_no_params_returns_legacy_reply_with_one_empty_page():
    _, callbacks = _handler_for([_Track([_ClipSlot(None), _ClipSlot(None)])], num_scenes=2)

    reply = callbacks[ADDRESS](())

    assert len(reply) == 1
    assert json.loads(reply[0]) == {
        "page_index": 0,
        "page_count": 1,
        "clips": {},
        "clip_colors": {},
    }


def test_out_of_range_page_index_returns_error_json_with_echoed_index():
    _, callbacks = _handler_for(
        [_Track([_ClipSlot(_Clip("Loop", 0x336699))])], num_scenes=1
    )

    reply = callbacks[ADDRESS]((5,))

    assert reply[0] == 5
    error = json.loads(reply[1])
    assert error["handler"] == "clip_grid"
    assert "out of range" in error["error"]
