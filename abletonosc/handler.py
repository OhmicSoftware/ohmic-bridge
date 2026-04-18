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

    #--------------------------------------------------------------------------------
    # Guarded variants of the generic callbacks.
    #
    # Use these for undocumented properties (e.g. scene.tempo,
    # song.root_note) whose presence and signature are not covered by
    # Ableton's stability contract. They mirror the per-handler
    # @guarded_lom decorator pattern: catch any exception, log a full
    # manually-formatted traceback (never logger.exception — see the
    # embedded-Python caveat noted on _call_method), and return an OSC
    # error tuple ("error: <ClassName>: <message>",) so Ohmic gets a
    # clean reply instead of timing out on silence.
    #
    # The plain _set_property / _call_method / _start_listen /
    # _stop_listen methods above remain unwrapped and are still the
    # right choice for documented properties — changing those would
    # add overhead for APIs that Ableton is contractually obliged to
    # keep working.
    #--------------------------------------------------------------------------------
    def _set_property_guarded(self, target, prop, params: Tuple):
        try:
            self.logger.info(
                "Setting property for %s: %s (new value %s)"
                % (self.class_identifier, prop, params[0])
            )
            setattr(target, prop, params[0])
            return None
        except Exception as e:
            try:
                tb = _traceback.format_exc()
            except Exception:
                tb = "(traceback unavailable)"
            self.logger.error(
                "%s set %s failed: %s: %s\n%s",
                self.class_identifier, prop, type(e).__name__, str(e), tb,
            )
            return ("error: " + type(e).__name__ + ": " + str(e),)

    def _start_listen_guarded(self, target, prop, params: Optional[Tuple] = (), getter=None):
        try:
            def property_changed_callback():
                try:
                    if getter is None:
                        value = getattr(target, prop)
                    else:
                        value = getter(params)
                    if type(value) is not tuple:
                        value = (value,)
                    self.logger.info(
                        "Property %s changed of %s %s: %s"
                        % (prop, self.class_identifier, str(params), value)
                    )
                    osc_address = "/live/%s/get/%s" % (self.class_identifier, prop)
                    self.osc_server.send(osc_address, (*params, *value,))
                except Exception as inner_e:
                    # The listener itself may fire after Ableton removes the
                    # property, long after _start_listen_guarded returned.
                    # Log and swallow so Live's listener machinery isn't
                    # poisoned by an unhandled exception inside a callback.
                    try:
                        tb = _traceback.format_exc()
                    except Exception:
                        tb = "(traceback unavailable)"
                    self.logger.error(
                        "%s listener %s fire failed: %s: %s\n%s",
                        self.class_identifier, prop,
                        type(inner_e).__name__, str(inner_e), tb,
                    )

            listener_key = (prop, tuple(params))
            if listener_key in self.listener_functions:
                self._stop_listen_guarded(target, prop, params)

            self.logger.info(
                "Adding listener for %s %s, property: %s"
                % (self.class_identifier, str(params), prop)
            )
            add_listener_function_name = "add_%s_listener" % prop
            add_listener_function = getattr(target, add_listener_function_name)
            add_listener_function(property_changed_callback)
            self.listener_functions[listener_key] = property_changed_callback
            self.listener_objects[listener_key] = target
            #--------------------------------------------------------------------------------
            # Immediately send the current value (same behavior as _start_listen).
            #--------------------------------------------------------------------------------
            property_changed_callback()
            return None
        except Exception as e:
            try:
                tb = _traceback.format_exc()
            except Exception:
                tb = "(traceback unavailable)"
            self.logger.error(
                "%s start_listen %s failed: %s: %s\n%s",
                self.class_identifier, prop, type(e).__name__, str(e), tb,
            )
            return ("error: " + type(e).__name__ + ": " + str(e),)

    def _stop_listen_guarded(self, target, prop, params: Optional[Tuple[Any]] = ()):
        try:
            listener_key = (prop, tuple(params))
            if listener_key in self.listener_functions:
                self.logger.info(
                    "Removing listener for %s %s, property %s"
                    % (self.class_identifier, str(params), prop)
                )
                listener_function = self.listener_functions[listener_key]
                remove_listener_function_name = "remove_%s_listener" % prop
                remove_listener_function = getattr(target, remove_listener_function_name)
                try:
                    remove_listener_function(listener_function)
                except Exception as e:
                    # This exception may be thrown when an observer is no
                    # longer connected. Benign — matches _stop_listen.
                    self.logger.info(
                        "Exception whilst removing listener (likely benign): %s" % e
                    )
                del self.listener_functions[listener_key]
                del self.listener_objects[listener_key]
            else:
                self.logger.warning(
                    "No listener function found for property: %s (%s)"
                    % (prop, str(params))
                )
            return None
        except Exception as e:
            try:
                tb = _traceback.format_exc()
            except Exception:
                tb = "(traceback unavailable)"
            self.logger.error(
                "%s stop_listen %s failed: %s: %s\n%s",
                self.class_identifier, prop, type(e).__name__, str(e), tb,
            )
            return ("error: " + type(e).__name__ + ": " + str(e),)


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
