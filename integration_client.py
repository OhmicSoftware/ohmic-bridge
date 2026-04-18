"""Simple OSC client helper used by the Bridge's integration tests.

Sends OSC messages to a running Ableton Live + Ohmic_Bridge pair on
127.0.0.1 and reads replies off the response port. Used ONLY by the
developer-run integration test suite (pytest -m integration); not
imported by the Bridge's Remote Script itself.

Reconstructed from AbletonOSC upstream MIT-licensed source. Defaults
bumped to 11002/11003 to match Ohmic Bridge rather than AbletonOSC's
11000/11001.

Lives alongside the richer ``client/`` package (used by
``run-console.py``) rather than replacing it. The integration test
suite needs a minimal ``recvfrom``-based client with an explicit
``BridgeNotResponding`` exception so developers get a clear error
when Ableton isn't running; the threaded-dispatcher client in
``client/`` is intentionally left untouched so ``run-console.py``
continues to work.
"""
import socket
from pythonosc.udp_client import SimpleUDPClient
from pythonosc import osc_packet


# Ableton Live tick is roughly 100ms; wait this long between a set
# and a read so the setter has actually landed.
TICK_DURATION = 0.125

OSC_LISTEN_PORT = 11003
OSC_SEND_PORT = 11002


class BridgeNotResponding(Exception):
    """Raised when the integration test suite can't reach the Bridge.

    Message is intentionally user-friendly because developers running
    integration tests may forget that Ableton needs to be up."""


class AbletonOSCClient:
    def __init__(self, hostname: str = "127.0.0.1",
                 send_port: int = OSC_SEND_PORT,
                 listen_port: int = OSC_LISTEN_PORT):
        self.client = SimpleUDPClient(hostname, send_port)
        self.listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listen_socket.bind(("0.0.0.0", listen_port))
        self.listen_socket.settimeout(2.0)

    def stop(self):
        self.listen_socket.close()

    def send_message(self, address: str, params=None):
        self.client.send_message(address, params or [])

    def drain(self):
        """Discard any pending datagrams on the listen socket. Used
        before each query so replies from prior fire-and-forget sends
        (for example an ``("error: ...",)`` tuple returned by a
        ``set_*`` handler that raised) cannot satisfy the new query —
        UDP has no per-request affinity and without this drain the
        stale reply reaches the caller instead."""
        self.listen_socket.settimeout(0.0)
        try:
            while True:
                self.listen_socket.recvfrom(65535)
        except (BlockingIOError, socket.timeout, OSError):
            pass
        self.listen_socket.settimeout(2.0)

    def query(self, address: str, params=None, timeout: float = 2.0):
        """Send a message and wait for a reply with a matching OSC
        address. Drains the listen socket first, but because the
        Bridge may queue echoes from prior fire-and-forget sends
        that arrive after the drain, we also keep reading until we
        see a reply whose address equals the one we sent — anything
        else is a leftover from a prior operation and is discarded."""
        self.drain()
        self.send_message(address, params)
        import time as _time
        deadline = _time.monotonic() + timeout
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                raise BridgeNotResponding(
                    "No response at address %s on port %d within %ss. "
                    "Is Ableton running with Ohmic Bridge loaded as a "
                    "Remote Script? (Settings -> Link, Tempo & MIDI -> "
                    "select Ohmic_Bridge)" % (address, OSC_LISTEN_PORT, timeout)
                )
            self.listen_socket.settimeout(remaining)
            try:
                data, _ = self.listen_socket.recvfrom(65535)
            except socket.timeout:
                raise BridgeNotResponding(
                    "No response at address %s on port %d within %ss. "
                    "Is Ableton running with Ohmic Bridge loaded as a "
                    "Remote Script?" % (address, OSC_LISTEN_PORT, timeout)
                )
            packet = osc_packet.OscPacket(data)
            msg = packet.messages[0].message
            if msg.address == address:
                return tuple(msg.params)
            # Leftover reply from a prior send — discard and keep reading.
