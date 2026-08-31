"""Unit tests for OSC server resilience against unsendable replies.

2026-08-30: a burst of EMSGSIZE errors from oversized reply datagrams
escaped ``OSCServer.send`` (which caught only ``BuildError``), aborted
the drain loop mid-tick every tick, and left the Bridge unable to
answer anything until the control surface was re-instantiated. These
tests pin the fix: a failed reply send is logged and dropped, and one
mishandled datagram never stops the messages queued behind it.
"""

import errno
import importlib

from tests.test_destructive_ack_unit import (
    PACKAGE_NAME,
    _stub_ableton_modules,
    _stub_bridge_package,
)


def _fresh_osc_server_module():
    _stub_ableton_modules()
    _stub_bridge_package()
    module = importlib.import_module(f"{PACKAGE_NAME}.abletonosc.osc_server")
    return importlib.reload(module)


class _ExplodingSendSocket:
    """recvfrom yields queued datagrams; every sendto raises EMSGSIZE."""

    def __init__(self, datagrams):
        self._datagrams = list(datagrams)
        self.sendto_attempts = 0

    def recvfrom(self, _bufsize):
        if self._datagrams:
            return self._datagrams.pop(0), ("127.0.0.1", 55555)
        raise OSError(errno.EWOULDBLOCK, "no more data")

    def sendto(self, _dgram, _addr):
        self.sendto_attempts += 1
        raise OSError(errno.EMSGSIZE, "Message too long")


def _server_with_socket(module, sock):
    server = module.OSCServer.__new__(module.OSCServer)
    server._socket = sock
    server._remote_addr = ("127.0.0.1", 11003)
    server._response_port = 11003
    server._callbacks = {}
    import logging

    server.logger = logging.getLogger("abletonosc-test")
    return server


def _datagram(module, address):
    builder = module.OscMessageBuilder(address)
    return builder.build().dgram


def test_unsendable_reply_is_dropped_not_raised():
    module = _fresh_osc_server_module()
    sock = _ExplodingSendSocket([])
    server = _server_with_socket(module, sock)

    server.send("/live/test/huge", ("x" * 100,))

    assert sock.sendto_attempts == 1


def test_reply_send_failure_does_not_starve_queued_datagrams():
    module = _fresh_osc_server_module()
    first = _datagram(module, "/live/test/replies_huge")
    second = _datagram(module, "/live/test/plain")
    sock = _ExplodingSendSocket([first, second])
    server = _server_with_socket(module, sock)

    handled = []
    server.add_handler(
        "/live/test/replies_huge",
        lambda params: ("y" * 100,),
    )
    server.add_handler(
        "/live/test/plain",
        lambda params: handled.append("plain") or None,
    )

    server.process()

    # The oversized reply was attempted and dropped, and the datagram
    # queued behind it still reached its handler in the same tick.
    assert sock.sendto_attempts == 1
    assert handled == ["plain"]


def test_handler_exception_does_not_starve_queued_datagrams():
    module = _fresh_osc_server_module()
    first = _datagram(module, "/live/test/raises")
    second = _datagram(module, "/live/test/plain")
    sock = _ExplodingSendSocket([first, second])
    server = _server_with_socket(module, sock)

    handled = []

    def _raises(_params):
        raise RuntimeError("handler blew up")

    server.add_handler("/live/test/raises", _raises)
    server.add_handler(
        "/live/test/plain",
        lambda params: handled.append("plain") or None,
    )

    server.process()

    assert handled == ["plain"]
