from ableton.v2.control_surface.component import Component
from typing import Optional, Tuple, Any
import logging
from .osc_server import OSCServer

class AbletonOSCHandler(Component):
    def __init__(self, manager):
        super().__init__()

        self.logger = logging.getLogger("abletonosc")
        self.manager = manager
        self.osc_server: OSCServer = self.manager.osc_server
        self.init_api()
        self.listener_functions = {}
        self.listener_objects = {}
        self.class_identifier = None

    def init_api(self):
        pass

    def clear_api(self):
        self._clear_listeners()

    #--------------------------------------------------------------------------------
    # Generic callbacks
    #--------------------------------------------------------------------------------
    def _call_method(self, target, method, params: Optional[Tuple] = ()):
        #--------------------------------------------------------------------------------
        # Exceptions raised here are caught by osc_server.py's process_message
        # outer handler and surfaced to the OSC client as an error. Do NOT add
        # a try/except inside this method — the Live-embedded Python's logger
        # raises `ValueError: substring not found` on self.logger.exception(...)
        # inside an except block, turning any genuine API error into a cryptic
        # substring error that masks the real cause. (Confirmed against
        # Live 12.3.5, April 2026.)
        #--------------------------------------------------------------------------------
        self.logger.info("Calling method for %s: %s (params %s)" % (self.class_identifier, method, str(params)))
        getattr(target, method)(*params)

    def _set_property(self, target, prop, params: Tuple) -> None:
        # Same logger caveat as _call_method — let exceptions propagate to
        # osc_server's outer handler rather than wrapping here.
        self.logger.info("Setting property for %s: %s (new value %s)" % (self.class_identifier, prop, params[0]))
        setattr(target, prop, params[0])

    def _get_property(self, target, prop, params: Optional[Tuple] = ()) -> Tuple[Any]:
        try:
            value = getattr(target, prop)
        except RuntimeError:
            #--------------------------------------------------------------------------------
            # Gracefully handle errors, which may occur when querying parameters that don't apply
            # to a particular object (e.g. track.fold_state for a non-group track)
            #--------------------------------------------------------------------------------
            value = None
        except AttributeError:
            #--------------------------------------------------------------------------------
            # Undocumented-property access can raise AttributeError if Ableton
            # renames or removes the property in a future release. Return None
            # as if the property were not applicable — matches the existing
            # RuntimeError behavior and keeps the Remote Script alive instead
            # of letting the AttributeError propagate. Do NOT call
            # self.logger.exception() here — see _call_method's note on the
            # embedded-Python logger quirk.
            #--------------------------------------------------------------------------------
            value = None
        self.logger.info("Getting property for %s: %s = %s" % (self.class_identifier, prop, value))
        return (value, *params)

    def _start_listen(self, target, prop, params: Optional[Tuple] = (), getter = None) -> None:
        """
        Start listening for the property named `prop` on the Live object `target`.
        `params` is typically a tuple containing the track/clip index.

        getter can be used for a customer getter when we're accessing native objects
        e.g. in view.py we don't return the selected_scene, but the selected_scene index.

        Args:
            target: 
            prop:
            params:
            getter:
        """
        def property_changed_callback():
            if getter is None:
                value = getattr(target, prop)
            else:
                value = getter(params)
            if type(value) is not tuple:
                value = (value,)
            self.logger.info("Property %s changed of %s %s: %s" % (prop, self.class_identifier, str(params), value))
            osc_address = "/live/%s/get/%s" % (self.class_identifier, prop)
            self.osc_server.send(osc_address, (*params, *value,))

        listener_key = (prop, tuple(params))
        if listener_key in self.listener_functions:
            self._stop_listen(target, prop, params)

        self.logger.info("Adding listener for %s %s, property: %s" % (self.class_identifier, str(params), prop))
        add_listener_function_name = "add_%s_listener" % prop
        add_listener_function = getattr(target, add_listener_function_name)
        add_listener_function(property_changed_callback)
        self.listener_functions[listener_key] = property_changed_callback
        self.listener_objects[listener_key] = target
        #--------------------------------------------------------------------------------
        # Immediately send the current value
        #--------------------------------------------------------------------------------
        property_changed_callback()

    def _stop_listen(self, target, prop, params: Optional[Tuple[Any]] = ()) -> None:
        listener_key = (prop, tuple(params))
        if listener_key in self.listener_functions:
            self.logger.info("Removing listener for %s %s, property %s" % (self.class_identifier, str(params), prop))
            listener_function = self.listener_functions[listener_key]
            remove_listener_function_name = "remove_%s_listener" % prop
            remove_listener_function = getattr(target, remove_listener_function_name)
            try:
                remove_listener_function(listener_function)
            except Exception as e:
                #--------------------------------------------------------------------------------
                # This exception may be thrown when an observer is no longer connected --
                # e.g., when trying to stop listening for a clip property of a clip that has been deleted.
                # Ignore as it is benign.
                #--------------------------------------------------------------------------------
                self.logger.info("Exception whilst removing listener (likely benign): %s" % e)

            del self.listener_functions[listener_key]
            del self.listener_objects[listener_key]
        else:
            self.logger.warning("No listener function found for property: %s (%s)" % (prop, str(params)))

    def _clear_listeners(self):
        """
        Clears all listener functions, to prevent listeners continuing to report after a reload.
        """
        for listener_key in list(self.listener_functions.keys())[:]:
            target = self.listener_objects[listener_key]
            prop, params = listener_key
            self._stop_listen(target, prop, params)


# ---------------------------------------------------------------------------
# Exception-wrapping decorators for undocumented LOM callbacks.
#
# Every handler that calls into Ableton's undocumented Live Object Model
# (get_notes_extended, root_note, cue_points, etc.) should be wrapped so
# an unexpected API change — a removed method, a signature drift, a new
# required argument — is caught and returned to Ohmic as an OSC error
# reply instead of propagating through Ableton's Python interpreter and
# crashing the Remote Script. Capability probing (see
# abletonosc/capabilities.py) catches PRESENCE changes; these decorators
# catch every other class of failure at call time.
#
# Wire format on error:
#   guarded_lom        -> ("error: <ClassName>: <message>",)
#   guarded_lom_json   -> (json.dumps({"error": ..., "handler": ...}),)
# ---------------------------------------------------------------------------
import json as _json
import traceback as _traceback
from functools import wraps as _wraps

_decorator_logger = logging.getLogger("abletonosc")


def guarded_lom(handler_name):
    """Wrap a tuple-returning OSC handler callback so unexpected
    exceptions are caught, logged, and reported to Ohmic.

    NOTE: Ableton's embedded Python logger raises ``ValueError:
    substring not found`` when ``logger.exception(...)`` is called
    inside an except block, masking the real exception. We log at
    ERROR level without auto-traceback and format the traceback
    manually instead, so the log still captures enough context to
    diagnose a broken LOM API without tripping the embedded-logger
    bug. (Confirmed against Live 12.3.5, April 2026.)"""
    def decorator(fn):
        @_wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                try:
                    tb = _traceback.format_exc()
                except Exception:
                    tb = "(traceback unavailable)"
                _decorator_logger.error(
                    "%s failed: %s: %s\n%s",
                    handler_name, type(e).__name__, str(e), tb,
                )
                return ("error: " + type(e).__name__ + ": " + str(e),)
        return wrapper
    return decorator


def guarded_lom_json(handler_name):
    """Wrap a JSON-returning OSC handler callback. Error path returns
    a JSON string with 'error' and 'handler' keys so callers that
    already json.loads() the success path can surface the error with
    the same parse. Uses the same manual-traceback pattern as
    guarded_lom (see note there)."""
    def decorator(fn):
        @_wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                try:
                    tb = _traceback.format_exc()
                except Exception:
                    tb = "(traceback unavailable)"
                _decorator_logger.error(
                    "%s failed: %s: %s\n%s",
                    handler_name, type(e).__name__, str(e), tb,
                )
                return (_json.dumps({
                    "error": type(e).__name__ + ": " + str(e),
                    "handler": handler_name,
                }),)
        return wrapper
    return decorator
