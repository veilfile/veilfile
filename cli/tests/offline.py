"""Keep the CLI test suites off the network: loopback connections only.

The suites stub out veilfile._urlopen, and the CLI's default base URL is
the live https://veilfile.com. A code path that bypasses the stub, or a
run against an older veilfile.py, would otherwise send real requests
there. Not a test module (unittest only collects test*.py).
"""

import ipaddress
import socket
from unittest import mock


class NetworkUsedInTest(AssertionError):
    """Raised instead of connecting. Not an OSError, so the CLI cannot
    mistake it for a network failure and the test fails loudly."""


def _loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def refuse_outbound_connections(testcase) -> None:
    """For the rest of `testcase`, refuse every non-loopback connection.

    socket.create_connection is what http.client (and so urllib) calls; it
    is checked before any DNS lookup. socket.socket.connect backs it up for
    anything that connects directly.
    """
    real_create_connection = socket.create_connection
    real_connect = socket.socket.connect

    def create_connection(address, *args, **kwargs):
        if not _loopback(address[0]):
            raise NetworkUsedInTest(f"test tried to connect to {address[0]}:{address[1]}")
        return real_create_connection(address, *args, **kwargs)

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            raise NetworkUsedInTest(f"test tried to connect to {address[0]}:{address[1]}")
        return real_connect(sock, address)

    for patcher in (
        mock.patch.object(socket, "create_connection", create_connection),
        mock.patch.object(socket.socket, "connect", connect),
    ):
        patcher.start()
        testcase.addCleanup(patcher.stop)
