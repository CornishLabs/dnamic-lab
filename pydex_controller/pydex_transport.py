"""Transport helpers for the PyDex command wire format.

This module intentionally knows only about the TCP framing used by PyDex:

    uint32 enum
    uint32 payload_length
    payload bytes

The command text inside the payload is handled by the command-listener template.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
import logging
import socket
import struct
import time
from typing import Optional

DEFAULT_ENCODING = "utf-8"
_UINT32 = struct.Struct("!L")


@dataclass(frozen=True)
class PydexFrame:
    enum: int
    text: str


class PydexTransportError(RuntimeError):
    """Raised when a PyDex frame cannot be read or written cleanly."""


def read_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise PydexTransportError(
                f"Connection closed while reading {size} bytes"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket, *, encoding: str = DEFAULT_ENCODING) -> PydexFrame:
    enum = _UINT32.unpack(read_exact(sock, _UINT32.size))[0]
    payload_size = _UINT32.unpack(read_exact(sock, _UINT32.size))[0]
    payload = read_exact(sock, payload_size)
    return PydexFrame(enum=enum, text=payload.decode(encoding))


def write_frame(
    sock: socket.socket,
    frame: PydexFrame,
    *,
    encoding: str = DEFAULT_ENCODING,
) -> None:
    payload = frame.text.encode(encoding)
    sock.sendall(_UINT32.pack(int(frame.enum)))
    sock.sendall(_UINT32.pack(len(payload)))
    sock.sendall(payload)


def receive_command_from_pydex(
    host: str,
    port: int,
    *,
    encoding: str = DEFAULT_ENCODING,
    connect_timeout_s: float = 2.0,
) -> PydexFrame:
    """Connect to PyDex, receive one command, echo it back, and close."""

    with socket.create_connection((host, port), timeout=connect_timeout_s) as sock:
        # Once connected, block until PyDex hands over the queued command.
        sock.settimeout(None)
        frame = read_frame(sock, encoding=encoding)
        write_frame(sock, frame, encoding=encoding)
        return frame


def poll_pydex_commands(
    host: str,
    port: int,
    *,
    encoding: str = DEFAULT_ENCODING,
    connect_timeout_s: float = 2.0,
    reconnect_delay_s: float = 1.0,
    logger: Optional[logging.Logger] = None,
) -> Iterator[PydexFrame]:
    """Yield command frames from PyDex forever."""

    log = logger or logging.getLogger(__name__)
    while True:
        try:
            yield receive_command_from_pydex(
                host,
                port,
                encoding=encoding,
                connect_timeout_s=connect_timeout_s,
            )
        except (ConnectionRefusedError, TimeoutError, OSError, PydexTransportError) as exc:
            log.debug(
                "No PyDex command available from %s:%s; retrying in %.3g s (%s)",
                host,
                port,
                reconnect_delay_s,
                exc,
            )
            time.sleep(reconnect_delay_s)


def serve_pydex_commands(
    host: str,
    port: int,
    frames: Iterable[PydexFrame],
    *,
    encoding: str = DEFAULT_ENCODING,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Serve queued command frames to a polling device controller.

    This is for local simulation. The real PyDex process already provides this
    side of the connection.
    """

    log = logger or logging.getLogger(__name__)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(1)
        log.info("Serving PyDex commands on %s:%s", host or "0.0.0.0", port)

        for frame in frames:
            log.info("Waiting for controller to fetch: %r", frame.text)
            conn, addr = server.accept()
            with conn:
                log.info("Controller connected from %s:%s", *addr)
                write_frame(conn, frame, encoding=encoding)
                reply = read_frame(conn, encoding=encoding)
                if reply != frame:
                    log.warning("Controller reply differed from sent frame: %r", reply)

        log.info("No more PyDex commands queued")
