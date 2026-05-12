"""Generic PyDex command listener template.

Copy this file for a specific device and fill in `setup()` and
`do_something_with_parsed_data()`.

See `pydex_controller/README.md` for the PyDex transport architecture and the
reason this command listener is conceptually a receiver but technically a TCP
client.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from typing import Any, Optional

try:
    from .pydex_transport import poll_pydex_commands
except ImportError:  # Allows running this file directly from this directory.
    from pydex_transport import poll_pydex_commands

LOGGER = logging.getLogger("pydex_controller.pydex_command_listener_template")

PYDEX_HOST = "192.168.23.10"
PYDEX_PORT = 8636


@dataclass(frozen=True)
class ParsedCommand:
    """A simple parsed representation of a PyDex command string."""

    name: str
    raw_payload: str
    payload: Any


@dataclass
class ControllerState:
    """Put device handles or runtime state here in a copied implementation."""

    dry_run: bool = True


def parse_message(message: str) -> ParsedCommand:
    """Parse a PyDex command string.

    Expected input is normally `command=payload`, for example:

        set_data=[[3, "time (us)", 10.0]]
        save_all=Z:\\Tweezer\\Experimental Results\\Run\\file.txt

    If the payload is valid JSON, `payload` is the decoded JSON object.
    Otherwise `payload` is left as the original string.
    """

    try:
        name, raw_payload = message.split("=", 1)
    except ValueError as exc:
        raise ValueError("Expected command in the form 'name=payload'") from exc

    name = name.strip()
    raw_payload = raw_payload.strip()
    if not name:
        raise ValueError("Command name was empty")

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        payload = raw_payload

    return ParsedCommand(name=name, raw_payload=raw_payload, payload=payload)


def setup(*, dry_run: bool = True) -> ControllerState:
    """Set up the device-specific controller state.

    In a real implementation this is where you would open hardware handles,
    load calibration/configuration files, or perform any initial device setup.
    """

    LOGGER.info("Running generic setup; dry_run=%s", dry_run)
    return ControllerState(dry_run=dry_run)


def do_something_with_parsed_data(
    state: ControllerState,
    command: ParsedCommand,
) -> None:
    """Apply one parsed command.

    Replace this function in a copied implementation. Keep parsing separate
    from actions so malformed commands can be logged cleanly without touching
    hardware.
    """

    LOGGER.info(
        "Received command %r with payload %r; no action implemented",
        command.name,
        command.payload,
    )

    if state.dry_run:
        LOGGER.info("Dry run enabled; leaving hardware unchanged")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=PYDEX_HOST, help="PyDex host to poll")
    parser.add_argument("--port", type=int, default=PYDEX_PORT, help="PyDex TCP port")
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=1.0,
        help="Seconds to wait after a failed connection",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=2.0,
        help="Seconds to wait when opening a TCP connection",
    )
    parser.add_argument(
        "--live",
        action="store_false",
        dest="dry_run",
        help="Pass dry_run=False into setup()",
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    return parser


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def listen_for_commands(args: argparse.Namespace, state: ControllerState) -> None:
    LOGGER.info("Polling PyDex commands from %s:%s", args.host, args.port)
    for frame in poll_pydex_commands(
        args.host,
        args.port,
        connect_timeout_s=args.connect_timeout,
        reconnect_delay_s=args.reconnect_delay,
        logger=LOGGER,
    ):
        LOGGER.info("Received PyDex frame enum=%s text=%r", frame.enum, frame.text)
        try:
            command = parse_message(frame.text)
            do_something_with_parsed_data(state, command)
        except Exception:
            LOGGER.exception("Failed to handle PyDex command: %r", frame.text)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.log_level)

    state = setup(dry_run=args.dry_run)
    try:
        listen_for_commands(args, state)
    except KeyboardInterrupt:
        LOGGER.info("Interrupted; shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
