from .handler import AbletonOSCHandler
from functools import partial
from typing import Tuple, Any

class SceneHandler(AbletonOSCHandler):
    def __init__(self, manager):
        super().__init__(manager)
        self.class_identifier = "scene"

    def init_api(self):
        # TODO: Needs unit tests

        def create_scene_callback(func, *args, include_ids: bool = False):
            def scene_callback(params: Tuple[Any]):
                scene_index = int(params[0])
                scene = self.song.scenes[scene_index]
                if (include_ids):
                    rv = func(scene, *args, params[0:])
                else:
                    rv = func(scene, *args, params[1:])

                if rv is not None:
                    return (scene_index, *rv)

            return scene_callback

        methods = [
            "fire",
            "fire_as_selected",
        ]
        properties_r = [
            "is_empty",
            "is_triggered",
        ]
        #--------------------------------------------------------------------------------
        # Documented read/write properties. Covered by Ableton's stability
        # contract; no per-handler guarding needed.
        #--------------------------------------------------------------------------------
        properties_rw_documented = [
            "color",
            "color_index",
            "name",
        ]
        #--------------------------------------------------------------------------------
        # Undocumented read/write properties (Live 11+ scene tempo and
        # time-signature overrides). If Ableton removes or changes these in
        # a future update, writes and listener registration need to surface
        # a clean OSC error to Ohmic rather than timing out silently. See
        # BRIDGE_LOM_AUDIT.md -> "Scene Tempo & Time Signature".
        #--------------------------------------------------------------------------------
        properties_rw_undocumented = [
            "tempo",
            "tempo_enabled",
            "time_signature_numerator",
            "time_signature_denominator",
            "time_signature_enabled",
        ]
        properties_rw = properties_rw_documented + properties_rw_undocumented

        for method in methods:
            self.osc_server.add_handler("/live/scene/%s" % method, create_scene_callback(self._call_method, method))

        # Reads: _get_property already catches AttributeError/RuntimeError
        # for the undocumented case and returns None, so no split needed.
        for prop in properties_r + properties_rw:
            self.osc_server.add_handler("/live/scene/get/%s" % prop,
                                        create_scene_callback(self._get_property, prop))

        # Listeners + writes: split documented (generic path) from
        # undocumented (guarded path) to get a clean error reply if
        # Ableton changes the underlying property.
        for prop in properties_r + properties_rw_documented:
            self.osc_server.add_handler("/live/scene/start_listen/%s" % prop,
                                        create_scene_callback(self._start_listen, prop, include_ids=True))
            self.osc_server.add_handler("/live/scene/stop_listen/%s" % prop,
                                        create_scene_callback(self._stop_listen, prop, include_ids=True))
        for prop in properties_rw_undocumented:
            self.osc_server.add_handler("/live/scene/start_listen/%s" % prop,
                                        create_scene_callback(self._start_listen_guarded, prop, include_ids=True))
            self.osc_server.add_handler("/live/scene/stop_listen/%s" % prop,
                                        create_scene_callback(self._stop_listen_guarded, prop, include_ids=True))
        for prop in properties_rw_documented:
            self.osc_server.add_handler("/live/scene/set/%s" % prop,
                                        create_scene_callback(self._set_property, prop))
        for prop in properties_rw_undocumented:
            self.osc_server.add_handler("/live/scene/set/%s" % prop,
                                        create_scene_callback(self._set_property_guarded, prop))
        
        #------------------------------------------------------------------------------------------------
        # The Live API does not have a `fire_selected` Scene method (or class method accessible from Python).
        # This block adds a `fire_selected` method that calls `fire_as_selected` on the selected scene.
        #------------------------------------------------------------------------------------------------
        def scene_fire_selected(params: Tuple[Any] = ()):
            selected_scene = self.song.view.selected_scene
            if selected_scene:
                selected_scene.fire_as_selected()

        self.osc_server.add_handler("/live/scene/fire_selected", scene_fire_selected)