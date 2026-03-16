import os
import sys
import tempfile
import Live
import json
from functools import partial
from typing import Tuple, Any

from .handler import AbletonOSCHandler

class SongHandler(AbletonOSCHandler):
    def __init__(self, manager):
        super().__init__(manager)
        self.class_identifier = "song"

    def init_api(self):
        #--------------------------------------------------------------------------------
        # Callbacks for Song: methods
        #--------------------------------------------------------------------------------
        for method in [
            "capture_and_insert_scene",
            "capture_midi",
            "continue_playing",
            "create_audio_track",
            "create_midi_track",
            "create_return_track",
            "create_scene",
            "delete_return_track",
            "delete_scene",
            "delete_track",
            "duplicate_scene",
            "duplicate_track",
            "force_link_beat_time",
            "jump_by",
            "jump_to_prev_cue",
            "jump_to_next_cue",
            "redo",
            "re_enable_automation",
            "set_or_delete_cue",
            "start_playing",
            "stop_all_clips",
            "stop_playing",
            "tap_tempo",
            "trigger_session_record",
            "undo"
        ]:
            callback = partial(self._call_method, self.song, method)
            self.osc_server.add_handler("/live/song/%s" % method, callback)

        #--------------------------------------------------------------------------------
        # Callbacks for Song: properties (read/write)
        #--------------------------------------------------------------------------------
        properties_rw = [
            "arrangement_overdub",
            "back_to_arranger",
            "clip_trigger_quantization",
            "current_song_time",
            "groove_amount",
            "is_ableton_link_enabled",
            "loop",
            "loop_length",
            "loop_start",
            "metronome",
            "midi_recording_quantization",
            "nudge_down",
            "nudge_up",
            "punch_in",
            "punch_out",
            "record_mode",
            "root_note",
            "scale_name",
            "session_record",
            "signature_denominator",
            "signature_numerator",
            "tempo"
        ]

        #--------------------------------------------------------------------------------
        # Callbacks for Song: properties (read-only)
        #--------------------------------------------------------------------------------
        properties_r = [
            "can_redo",
            "can_undo",
            "is_playing",
            "name",
            "file_path",
            "song_length",
            "session_record_status"
        ]

        for prop in properties_r + properties_rw:
            self.osc_server.add_handler("/live/song/get/%s" % prop, partial(self._get_property, self.song, prop))
            self.osc_server.add_handler("/live/song/start_listen/%s" % prop, partial(self._start_listen, self.song, prop))
            self.osc_server.add_handler("/live/song/stop_listen/%s" % prop, partial(self._stop_listen, self.song, prop))
        for prop in properties_rw:
            self.osc_server.add_handler("/live/song/set/%s" % prop, partial(self._set_property, self.song, prop))

        #--------------------------------------------------------------------------------
        # Callbacks for Song: Track properties
        #--------------------------------------------------------------------------------
        self.osc_server.add_handler("/live/song/get/num_tracks", lambda _: (len(self.song.tracks),))

        def song_get_track_names(params):
            if len(params) == 0:
                track_index_min, track_index_max = 0, len(self.song.tracks)
            else:
                track_index_min, track_index_max = params
                if track_index_max == -1:
                    track_index_max = len(self.song.tracks)
            return tuple(self.song.tracks[index].name for index in range(track_index_min, track_index_max))
        self.osc_server.add_handler("/live/song/get/track_names", song_get_track_names)

        #--------------------------------------------------------------------------------
        # Bulk endpoints for session grid (optimized — fewer OSC round-trips)
        #--------------------------------------------------------------------------------
        def session_info(_):
            """Return track metadata + song scale in one call.

            Response: JSON string with track_names, track_colors, midi_tracks,
            num_scenes, root_note, scale_name, tempo.
            """
            tracks = self.song.tracks
            num_scenes = len(self.song.scenes)
            track_names = []
            track_colors = []
            midi_tracks = []
            for track in tracks:
                track_names.append(track.name)
                c = track.color
                track_colors.append("#%02x%02x%02x" % ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF))
                midi_tracks.append(bool(track.has_midi_input))
            data = {
                "track_names": track_names,
                "track_colors": track_colors,
                "midi_tracks": midi_tracks,
                "num_scenes": num_scenes,
                "root_note": self.song.root_note,
                "scale_name": self.song.scale_name,
                "tempo": self.song.tempo,
            }
            return (json.dumps(data),)
        self.osc_server.add_handler("/live/song/get/session_info", session_info)

        def clip_grid(_):
            """Return occupied clip slots with names and colors in one call.

            Response: JSON string with clips dict and clip_colors dict,
            keyed by "track_idx,slot_idx".
            """
            tracks = self.song.tracks
            num_scenes = len(self.song.scenes)
            clips = {}
            clip_colors = {}
            for track_idx, track in enumerate(tracks):
                for slot_idx, clip_slot in enumerate(track.clip_slots):
                    if slot_idx >= num_scenes:
                        break
                    if clip_slot.clip is not None:
                        key = "%d,%d" % (track_idx, slot_idx)
                        name = clip_slot.clip.name
                        clips[key] = name if name else "(unnamed)"
                        c = clip_slot.clip.color
                        clip_colors[key] = "#%02x%02x%02x" % ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
            data = {
                "clips": clips,
                "clip_colors": clip_colors,
            }
            return (json.dumps(data),)
        self.osc_server.add_handler("/live/song/get/clip_grid", clip_grid)

        def song_get_track_data(params):
            """
            Retrieve one more properties of a block of tracks and their clips.
            Properties must be of the format track.property_name or clip.property_name.

            For example:
                /live/song/get/track_data 0 12 track.name clip.name clip.length

            Queries tracks 0..11, and returns a list of values comprising:

            [track_0_name, clip_0_0_name,   clip_0_1_name,   ... clip_0_7_name,
                           clip_1_0_length, clip_0_1_length, ... clip_0_7_length,
             track_1_name, clip_1_0_name,   clip_1_1_name,   ... clip_1_7_name, ...]
            """
            track_index_min, track_index_max, *properties = params
            track_index_min = int(track_index_min)
            track_index_max = int(track_index_max)
            self.logger.info("Getting track data: %s (tracks %d..%d)" %
                             (properties, track_index_min, track_index_max))
            if track_index_max == -1:
                track_index_max = len(self.song.tracks)
            rv = []
            for track_index in range(track_index_min, track_index_max):
                track = self.song.tracks[track_index]
                for prop in properties:
                    obj, property_name = prop.split(".")
                    if obj == "track":
                        if property_name == "num_devices":
                            value = len(track.devices)
                        else:
                            value = getattr(track, property_name)
                            if isinstance(value, Live.Track.Track):
                                #--------------------------------------------------------------------------------
                                # Map Track objects to their track_index to return via OSC
                                #--------------------------------------------------------------------------------
                                value = list(self.song.tracks).index(value)
                        rv.append(value)
                    elif obj == "clip":
                        for clip_slot in track.clip_slots:
                            if clip_slot.clip is not None:
                                rv.append(getattr(clip_slot.clip, property_name))
                            else:
                                rv.append(None)
                    elif obj == "clip_slot":
                        for clip_slot in track.clip_slots:
                            rv.append(getattr(clip_slot, property_name))
                    elif obj == "device":
                        for device in track.devices:
                            rv.append(getattr(device, property_name))
                    else:
                        self.logger.error("Unknown object identifier in get/track_data: %s" % obj)
            return tuple(rv)
        self.osc_server.add_handler("/live/song/get/track_data", song_get_track_data)


        def song_export_structure(params):
            tracks = []
            for track_index, track in enumerate(self.song.tracks):
                group_track = None
                if track.group_track is not None:
                    group_track = list(self.song.tracks).index(track.group_track)
                track_data = {
                    "index": track_index,
                    "name": track.name,
                    "is_foldable": track.is_foldable,
                    "group_track": group_track,
                    "clips": [],
                    "devices": []
                }
                for clip_index, clip_slot in enumerate(track.clip_slots):
                    if clip_slot.clip:
                        clip_data = {
                            "index": clip_index,
                            "name": clip_slot.clip.name,
                            "length": clip_slot.clip.length,
                        }
                        track_data["clips"].append(clip_data)

                for device_index, device in enumerate(track.devices):
                    device_data = {
                        "class_name": device.class_name,
                        "type": device.type,
                        "name": device.name,
                        "parameters": []
                    }
                    for parameter in device.parameters:
                        device_data["parameters"].append({
                            "name": parameter.name,
                            "value": parameter.value,
                            "min": parameter.min,
                            "max": parameter.max,
                            "is_quantized": parameter.is_quantized,
                        })
                    track_data["devices"].append(device_data)

                tracks.append(track_data)
            song = {
                "tracks": tracks
            }

            if sys.platform == "darwin":
                #--------------------------------------------------------------------------------
                # On macOS, TMPDIR by default points to a process-specific directory.
                # We want to use a global temp dir (typically, tmp) so that other processes
                # know where to find this output .json, so unset TMPDIR.
                #--------------------------------------------------------------------------------
                os.environ["TMPDIR"] = ""
            fd = open(os.path.join(tempfile.gettempdir(), "abletonosc-song-structure.json"), "w")
            json.dump(song, fd)
            fd.close()
            self.logger.warning("Exported song structure to directory %s" % tempfile.gettempdir())
            return (1,)
        self.osc_server.add_handler("/live/song/export/structure", song_export_structure)

        #--------------------------------------------------------------------------------
        # Callbacks for Song: Scene properties
        #--------------------------------------------------------------------------------
        self.osc_server.add_handler("/live/song/get/num_scenes", lambda _: (len(self.song.scenes),))

        def song_get_scene_names(params):
            if len(params) == 0:
                scene_index_min, scene_index_max = 0, len(self.song.scenes)
            else:
                scene_index_min, scene_index_max = params
            return tuple(self.song.scenes[index].name for index in range(scene_index_min, scene_index_max))
        self.osc_server.add_handler("/live/song/get/scenes/name", song_get_scene_names)

        #--------------------------------------------------------------------------------
        # Callbacks for Song: Cue point properties
        #--------------------------------------------------------------------------------
        def song_get_cue_points(song, _):
            cue_points = song.cue_points
            cue_point_pairs = [(cue_point.name, cue_point.time) for cue_point in cue_points]
            return tuple(element for pair in cue_point_pairs for element in pair)
        self.osc_server.add_handler("/live/song/get/cue_points", partial(song_get_cue_points, self.song))

        def song_jump_to_cue_point(song, params: Tuple[Any] = ()):
            cue_point_index = params[0]
            if isinstance(cue_point_index, str):
                for cue_point in song.cue_points:
                    if cue_point.name == cue_point_index:
                        cue_point.jump()
            elif isinstance(cue_point_index, int):
                cue_point = song.cue_points[cue_point_index]
                cue_point.jump()
        self.osc_server.add_handler("/live/song/cue_point/jump", partial(song_jump_to_cue_point, self.song))

        self.osc_server.add_handler("/live/song/cue_point/add_or_delete", partial(self._call_method, self.song, "set_or_delete_cue"))
        def song_cue_point_set_name(song, params: Tuple[Any] = ()):
            cue_point_index = params[0]
            new_name = params[1]
            cue_point = song.cue_points[cue_point_index]
            cue_point.name = new_name
        self.osc_server.add_handler("/live/song/cue_point/set/name", partial(song_cue_point_set_name, self.song))

        #--------------------------------------------------------------------------------
        # Listener for /live/song/get/beat
        #--------------------------------------------------------------------------------
        self.last_song_time = -1.0
        
        def stop_beat_listener(params: Tuple[Any] = ()):
            try:
                self.song.remove_current_song_time_listener(self.current_song_time_changed)
                self.logger.info("Removing beat listener")
            except:
                pass

        def start_beat_listener(params: Tuple[Any] = ()):
            stop_beat_listener()
            self.logger.info("Adding beat listener")
            self.song.add_current_song_time_listener(self.current_song_time_changed)

        self.osc_server.add_handler("/live/song/start_listen/beat", start_beat_listener)
        self.osc_server.add_handler("/live/song/stop_listen/beat", stop_beat_listener)

    def current_song_time_changed(self):
        #--------------------------------------------------------------------------------
        # If song has rewound or skipped to next beat, sent a /live/beat message
        #--------------------------------------------------------------------------------
        if (self.song.current_song_time < self.last_song_time) or \
                (int(self.song.current_song_time) > int(self.last_song_time)):
            self.osc_server.send("/live/song/get/beat", (int(self.song.current_song_time),))
        self.last_song_time = self.song.current_song_time

    def clear_api(self):
        super().clear_api()
        try:
            self.song.remove_current_song_time_listener(self.current_song_time_changed)
        except:
            pass
