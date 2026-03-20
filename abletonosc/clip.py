import re
from typing import Tuple, Callable, Any, Optional
from .handler import AbletonOSCHandler
import Live

def note_name_to_midi(name):
    """ Maps a MIDI note name (D3, C#6) to a value.
    Assumes that middle C is C4. """
    note_names = [["C"],
                  ["C#", "Db"],
                  ["D"],
                  ["D#", "Eb"],
                  ["E"],
                  ["F"],
                  ["F#", "Gb"],
                  ["G"],
                  ["G#", "Ab"],
                  ["A"],
                  ["A#", "Bb"],
                  ["B"]]

    for index, names in enumerate(note_names):
        if name in names:
            return index
    return None

class ClipHandler(AbletonOSCHandler):
    def __init__(self, manager):
        super().__init__(manager)
        self.class_identifier = "clip"
        self._clip_notes_cache = []

    def init_api(self):
        def create_clip_callback(func, *args, pass_clip_index=False):
            """
            Creates a callback that expects the following set of arguments:
              (track_index, clip_index, *args)

            The callback then extracts the relevant `Clip` object from the current Song,
            and calls `func` with this `Clip` object plus any additional *args.

            pass_clip_index is a bit of an ugly hack, although seems like the lesser of
            evils for scenarios where the track/clip index is needed (as a clip is unable
            to query its own index). Other alternatives include _always_ passing track/clip
            index to the callback, but this adds arg clutter to every single callback.
            """

            def clip_callback(params: Tuple[Any]) -> Tuple:
                #--------------------------------------------------------------------------------
                # Cast to int to support clients such as TouchOSC that, by default, pass all
                # numeric arguments as float.
                #--------------------------------------------------------------------------------
                track_index, clip_index = int(params[0]), int(params[1])
                track = self.song.tracks[track_index]
                clip = track.clip_slots[clip_index].clip
                if pass_clip_index:
                    rv = func(clip, *args, tuple(params[0:]))
                else:
                    rv = func(clip, *args, tuple(params[2:]))

                if rv is not None:
                    return (track_index, clip_index, *rv)

            return clip_callback

        methods = [
            "fire",
            "stop",
            "duplicate_loop", 
            "remove_notes_by_id"
        ]
        properties_r = [
            "end_time",
            "file_path",
            "gain_display_string",
            "has_groove",
            "is_midi_clip",
            "is_audio_clip",
            "is_overdubbing",
            "is_playing",
            "is_recording",
            "is_triggered",
            "length",
            "playing_position",
            "sample_length",
            "start_time",
            "will_record_on_start"
            ## TODO list:
            ##"groove", ## if other than None, says "Error handling OSC message: Infered arg_value type is not supported"
            ## is_arrangement_clip            
            ##"warp_markers", ## "Infered arg_value type is not supported"
            ##"view", ##"Infered arg_value type is not supported"
        ]
        properties_rw = [
            "color",
            "color_index",
            "end_marker",
            "gain",
            "launch_mode",
            "launch_quantization",
            "legato",
            "loop_end",
            "loop_start",
            "looping",
            "muted",
            "name",
            "pitch_coarse",
            "pitch_fine",
            "position",
            "ram_mode",
            "start_marker",
            "velocity_amount",
            "warp_mode",
            "warping",
        ]

        for method in methods:
            self.osc_server.add_handler("/live/clip/%s" % method,
                                        create_clip_callback(self._call_method, method))

        for prop in properties_r + properties_rw:
            self.osc_server.add_handler("/live/clip/get/%s" % prop,
                                        create_clip_callback(self._get_property, prop))
            self.osc_server.add_handler("/live/clip/start_listen/%s" % prop,
                                        create_clip_callback(self._start_listen, prop, pass_clip_index=True))
            self.osc_server.add_handler("/live/clip/stop_listen/%s" % prop,
                                        create_clip_callback(self._stop_listen, prop, pass_clip_index=True))
        for prop in properties_rw:
            self.osc_server.add_handler("/live/clip/set/%s" % prop,
                                        create_clip_callback(self._set_property, prop))

        def clip_get_notes(clip, params: Tuple[Any] = ()):
            if len(params) == 4:
                pitch_start, pitch_span, time_start, time_span = params
            elif len(params) == 0:
                pitch_start, pitch_span, time_start, time_span = 0, 127, -8192, 16384
            else:
                raise ValueError("Invalid number of arguments for /clip/get/notes. Either 0 or 4 arguments must be passed.")
            notes = clip.get_notes_extended(pitch_start, pitch_span, time_start, time_span)
            all_note_attributes = []
            for note in notes:
                probability = note.probability if hasattr(note, 'probability') else 1.0
                all_note_attributes += [note.pitch, note.start_time, note.duration, note.velocity, note.mute, probability]
            return tuple(all_note_attributes)

        def clip_add_notes(clip, params: Tuple[Any] = ()):
            notes = []
            # Support both 5-param (legacy) and 6-param (with probability) formats
            step = 6 if len(params) >= 6 and len(params) % 6 == 0 else 5
            for offset in range(0, len(params), step):
                chunk = params[offset:offset + step]
                pitch, start_time, duration, velocity, mute = chunk[0], chunk[1], chunk[2], chunk[3], chunk[4]
                probability = float(chunk[5]) if step == 6 else 1.0
                note = Live.Clip.MidiNoteSpecification(start_time=start_time,
                                                       duration=duration,
                                                       pitch=pitch,
                                                       velocity=velocity,
                                                       mute=mute,
                                                       probability=probability)
                notes.append(note)
            clip.add_new_notes(tuple(notes))

        def clip_remove_notes(clip, params: Tuple[Any] = ()):
            if len(params) == 4:
                pitch_start, pitch_span, time_start, time_span = params
            elif len(params) == 0:
                pitch_start, pitch_span, time_start, time_span = 0, 127, -8192, 16384
            else:
                raise ValueError("Invalid number of arguments for /clip/remove/notes. Either 0 or 4 arguments must be passed.")
            clip.remove_notes_extended(pitch_start, pitch_span, time_start, time_span)

        self.osc_server.add_handler("/live/clip/get/notes", create_clip_callback(clip_get_notes))
        self.osc_server.add_handler("/live/clip/add/notes", create_clip_callback(clip_add_notes))
        self.osc_server.add_handler("/live/clip/remove/notes", create_clip_callback(clip_remove_notes))

        # -------------------------------------------------------------------
        # MIDI CC envelope endpoints
        #
        # Read/write/clear automation envelopes for MIDI CC parameters.
        # CC envelopes are accessed through the clip's automation system.
        # The track (clip.canonical_parent.canonical_parent) provides
        # the device parameters that map to MIDI CCs.
        # -------------------------------------------------------------------

        def _find_cc_param(clip, cc_number):
            """Find the DeviceParameter for a MIDI CC number.

            Searches the track's device chain for a parameter whose name
            matches the CC number (e.g. "Mod Wheel", "CC 1").
            Returns the parameter or None.
            """
            try:
                # Navigate: Clip -> ClipSlot -> Track
                track = clip.canonical_parent.canonical_parent
            except AttributeError:
                return None

            # Search all devices on the track for a matching CC parameter
            for device in track.devices:
                for param in device.parameters:
                    try:
                        name = param.name
                        # Match patterns like "CC 1", "CC1", "Mod Wheel" etc.
                        if name == "CC %d" % cc_number or name == "CC%d" % cc_number:
                            return param
                    except Exception:
                        continue

            # Also check mixer device for standard CC mappings
            try:
                mixer = track.mixer_device
                if cc_number == 7:
                    return mixer.volume
                elif cc_number == 10:
                    return mixer.panning
            except Exception:
                pass

            return None

        def clip_get_envelope(clip, params: Tuple[Any] = ()):
            """Read CC automation envelope by sampling at regular intervals.

            params: (cc_number,)
            Returns: flat tuple of (time1, value1, time2, value2, ...)
            """
            if len(params) < 1:
                raise ValueError("cc_number is required for /clip/get/envelope")
            cc_number = int(params[0])
            clip_length = clip.length

            # Strategy 1: Search existing envelopes by parameter name
            target_env = None
            param = _find_cc_param(clip, cc_number)
            if param is not None:
                try:
                    target_env = clip.automation_envelope(param)
                except Exception:
                    pass

            # Strategy 2: Iterate existing envelopes
            if target_env is None:
                for env in clip.automation_envelopes:
                    try:
                        p = env.parameter
                        p_name = str(getattr(p, 'name', ''))
                        if ("CC %d" % cc_number) in p_name or ("CC%d" % cc_number) in p_name:
                            target_env = env
                            break
                    except Exception:
                        continue

            if target_env is None:
                return ()

            # Sample the envelope at 8 points per beat
            points_per_beat = 8
            num_samples = max(1, int(clip_length * points_per_beat))
            data = []
            for i in range(num_samples + 1):
                time = i / points_per_beat
                if time > clip_length:
                    time = clip_length
                try:
                    value = target_env.value_at_time(time)
                except Exception:
                    value = 0.0
                data.append(time)
                data.append(value)

            return tuple(data)

        def clip_set_envelope(clip, params: Tuple[Any] = ()):
            """Write CC automation envelope to a clip.

            params: (cc_number, time1, value1, time2, value2, ...)
            Clears existing envelope, then writes all points via insert_step().
            """
            if len(params) < 3:
                raise ValueError("cc_number and at least one (time, value) pair required")
            cc_number = int(params[0])
            points = params[1:]

            if len(points) % 2 != 0:
                raise ValueError("Points must be (time, value) pairs")

            # Find the CC parameter
            param = _find_cc_param(clip, cc_number)
            if param is None:
                self.logger.warning("Could not find parameter for CC %d" % cc_number)
                return (-1,)

            # Clear existing envelope
            try:
                clip.clear_envelope(param)
            except Exception as e:
                self.logger.warning("clear_envelope failed for CC %d: %s" % (cc_number, e))

            # Get or create the envelope
            try:
                envelope = clip.automation_envelope(param)
            except Exception as e:
                self.logger.error("automation_envelope failed for CC %d: %s" % (cc_number, e))
                return (-1,)

            # Write all points
            num_points = 0
            for i in range(0, len(points), 2):
                time = float(points[i])
                value = float(points[i + 1])
                try:
                    envelope.insert_step(time, value, 0)
                    num_points += 1
                except Exception as e:
                    self.logger.error("insert_step failed at time %.2f: %s" % (time, e))

            return (num_points,)

        def clip_clear_envelope(clip, params: Tuple[Any] = ()):
            """Clear CC automation envelope from a clip.

            params: (cc_number,)
            """
            if len(params) < 1:
                raise ValueError("cc_number is required for /clip/clear/envelope")
            cc_number = int(params[0])

            param = _find_cc_param(clip, cc_number)
            if param is None:
                self.logger.warning("Could not find parameter for CC %d to clear" % cc_number)
                return (-1,)

            try:
                clip.clear_envelope(param)
                return (1,)
            except Exception as e:
                self.logger.error("clear_envelope failed for CC %d: %s" % (cc_number, e))
                return (-1,)

        self.osc_server.add_handler("/live/clip/get/envelope", create_clip_callback(clip_get_envelope))
        self.osc_server.add_handler("/live/clip/set/envelope", create_clip_callback(clip_set_envelope))
        self.osc_server.add_handler("/live/clip/clear/envelope", create_clip_callback(clip_clear_envelope))

        # -------------------------------------------------------------------
        # Arrangement clip endpoints
        #
        # Arrangement clips are accessed via track.arrangement_clips[index],
        # NOT via clip_slots. Keep these handlers fully separate from session
        # clip handlers — they may diverge in behavior over time.
        # -------------------------------------------------------------------

        def create_arrangement_clip_callback(func):
            def arrangement_clip_callback(params: Tuple[Any]) -> Tuple:
                track_index, clip_index = int(params[0]), int(params[1])
                track = self.song.tracks[track_index]
                clips = track.arrangement_clips
                if clip_index < 0 or clip_index >= len(clips):
                    return None
                clip = clips[clip_index]
                rv = func(clip, tuple(params[2:]))
                if rv is not None:
                    return (track_index, clip_index, *rv)
            return arrangement_clip_callback

        def arrangement_clip_get_notes(clip, params: Tuple[Any] = ()):
            if len(params) == 4:
                pitch_start, pitch_span, time_start, time_span = params
            elif len(params) == 0:
                pitch_start, pitch_span, time_start, time_span = 0, 127, -8192, 16384
            else:
                raise ValueError("Invalid number of arguments for /arrangement_clip/get/notes. Either 0 or 4 arguments must be passed.")
            notes = clip.get_notes_extended(pitch_start, pitch_span, time_start, time_span)
            all_note_attributes = []
            for note in notes:
                probability = note.probability if hasattr(note, 'probability') else 1.0
                all_note_attributes += [note.pitch, note.start_time, note.duration, note.velocity, note.mute, probability]
            return tuple(all_note_attributes)

        self.osc_server.add_handler("/live/arrangement_clip/get/notes", create_arrangement_clip_callback(arrangement_clip_get_notes))

        def arrangement_clip_add_notes(clip, params: Tuple[Any] = ()):
            notes = []
            step = 6 if len(params) >= 6 and len(params) % 6 == 0 else 5
            for offset in range(0, len(params), step):
                chunk = params[offset:offset + step]
                pitch, start_time, duration, velocity, mute = chunk[0], chunk[1], chunk[2], chunk[3], chunk[4]
                probability = float(chunk[5]) if step == 6 else 1.0
                note = Live.Clip.MidiNoteSpecification(start_time=float(start_time),
                                                       duration=float(duration),
                                                       pitch=int(pitch),
                                                       velocity=float(velocity),
                                                       mute=bool(mute),
                                                       probability=probability)
                notes.append(note)
            try:
                clip.add_new_notes(tuple(notes))
                return (len(notes),)
            except Exception as e:
                self.logger.error("arrangement_clip_add_notes FAILED: %s" % e)
                return (-1, str(e))

        self.osc_server.add_handler("/live/arrangement_clip/add/notes", create_arrangement_clip_callback(arrangement_clip_add_notes))

        def arrangement_clip_remove_notes(clip, params: Tuple[Any] = ()):
            if len(params) == 4:
                pitch_start, pitch_span, time_start, time_span = params
            elif len(params) == 0:
                pitch_start, pitch_span, time_start, time_span = 0, 127, -8192, 16384
            else:
                raise ValueError("Invalid number of arguments for /arrangement_clip/remove/notes. Either 0 or 4 arguments must be passed.")
            clip.remove_notes_extended(pitch_start, pitch_span, time_start, time_span)

        self.osc_server.add_handler("/live/arrangement_clip/remove/notes", create_arrangement_clip_callback(arrangement_clip_remove_notes))

        def arrangement_clip_create(params: Tuple[Any]) -> Tuple:
            track_index = int(params[0])
            start_time = float(params[1])
            length = float(params[2])
            track = self.song.tracks[track_index]
            track.create_midi_clip(start_time, length)
            return (track_index, start_time, length)

        self.osc_server.add_handler("/live/arrangement_clip/create", arrangement_clip_create)

        def arrangement_clip_delete(params: Tuple[Any]) -> Tuple:
            track_index = int(params[0])
            clip_index = int(params[1])
            track = self.song.tracks[track_index]
            clips = track.arrangement_clips
            if clip_index < 0 or clip_index >= len(clips):
                return None
            track.delete_clip(clips[clip_index])
            return (track_index, clip_index)

        self.osc_server.add_handler("/live/arrangement_clip/delete", arrangement_clip_delete)

        def clips_filter_handler(params: Tuple):
            # TODO: Pre-cache clip notes
            if len(self._clip_notes_cache) == 0:
                self.logger.warning("Building clip notes cache...")
                self._build_clip_name_cache()
            else:
                self.logger.warning("Found existing clip notes cache (len = %d)" % len(self._clip_notes_cache))
            note_indices = [note_name_to_midi(name) for name in params]

            self.logger.warning("Got note indices: %s" % note_indices)
            for track_index, track in enumerate(self.song.tracks):
                for clip_slot_index, clip_slot in enumerate(track.clip_slots):
                    clip_notes_list = self._clip_notes_cache[track_index][clip_slot_index]
                    if clip_notes_list:
                        clip = clip_slot.clip
                        if all(note in note_indices for note in clip_notes_list):
                            clip.muted = False
                        else:
                            clip.muted = True

        self.osc_server.add_handler("/live/clips/filter", clips_filter_handler)

        def clips_unfilter_handler(params: Tuple):
            track_start = params[0] if len(params) > 0 else 0
            track_end = params[1] if len(params) > 1 else len(self.song.tracks)

            self.logger.info("Unfiltering tracks: %d .. %d" % (track_start, track_end))
            for track in self.song.tracks[track_start:track_end]:
                for clip_slot in track.clip_slots:
                    if clip_slot.has_clip:
                        clip = clip_slot.clip
                        clip.muted = False

        self.osc_server.add_handler("/live/clips/unfilter", clips_unfilter_handler)

    def _build_clip_name_cache(self):
        regex = "([_-])([A-G][A-G#b1-9-]*)$"
        for track_index, track in enumerate(self.song.tracks):
            self._clip_notes_cache.append([])
            for clip_slot_index, clip_slot in enumerate(track.clip_slots):
                self._clip_notes_cache[-1].append([])
                if clip_slot.has_clip:
                    clip = clip_slot.clip
                    clip_name = clip.name
                    match = re.search(regex, clip_name)
                    if match:
                        clip_notes_str = match.group(2)
                        clip_notes_str = re.sub("[1-9]", "", clip_notes_str)
                        clip_notes_list = clip_notes_str.split("-")
                        clip_notes_list = [note_name_to_midi(name) for name in clip_notes_list]
                        self._clip_notes_cache[-1][-1] = clip_notes_list
