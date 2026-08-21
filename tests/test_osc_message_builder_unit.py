from pythonosc.osc_message_builder import OscMessageBuilder


def test_builder_round_trips_clip_pointer_above_signed_int32():
    clip_pointer = 1 << 31
    builder = OscMessageBuilder("/live/song/get/track_data")
    builder.add_arg(clip_pointer)

    assert builder.build().params == [clip_pointer]
