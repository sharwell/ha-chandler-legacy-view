"""Verify that real-framework tests cannot use external storage or networks."""

import socket

import pytest
from pytest_socket import SocketBlockedError, SocketConnectBlockedError


async def test_storage_outside_test_directory_is_rejected(hass_factory, tmp_path):
    with pytest.raises(ValueError, match="inside this test's directory"):
        await hass_factory(tmp_path.parent)


def test_external_socket_connection_is_blocked():
    # A documentation-only address is still rejected before any connection is
    # attempted. Both socket creation and connection guards are valid barriers.
    with pytest.warns(UserWarning, match="A test tried to use socket"), pytest.raises(
        (SocketBlockedError, SocketConnectBlockedError)
    ):
        with socket.socket() as connection:
            connection.connect(("192.0.2.1", 9))


@pytest.mark.parametrize("operation,args", [
    ("connect", (("127.0.0.1", 9),)),
    ("connect_ex", (("127.0.0.1", 9),)),
    ("bind", (("127.0.0.1", 0),)),
    ("listen", (1,)),
    ("sendto", (b"test", ("127.0.0.1", 9))),
])
async def test_real_runtime_blocks_local_host_socket_io(hass, operation, args):
    with socket.socket() as connection:
        with pytest.raises(PermissionError, match="Host socket I/O is disabled"):
            getattr(connection, operation)(*args)
