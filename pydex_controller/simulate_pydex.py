"""Simulate the PyDex command server without Qt."""

from __future__ import annotations

import argparse
import logging
from typing import Optional

try:
    from .pydex_transport import PydexFrame, serve_pydex_commands
except ImportError:  # Allows `python simulate_pydex.py` from this directory.
    from pydex_transport import PydexFrame, serve_pydex_commands

DEFAULT_MESSAGES = [
    'set_data=[[3,"time (us)",10.0]]',
    r"save_all=Z:\Tweezer\Experimental Results\TestRun\RFSOCparam4.txt",
]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default="",
        help="Interface to bind; empty string means all interfaces",
    )
    parser.add_argument("--port", type=int, default=8636, help="TCP port to bind")
    parser.add_argument(
        "--message",
        action="append",
        dest="messages",
        help="Command payload to queue; may be supplied more than once",
    )
    parser.add_argument(
        "--enum",
        type=int,
        default=1,
        help="PyDex enum value to send with each message",
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    return parser


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.log_level)

    frames = [
        PydexFrame(enum=args.enum, text=message)
        for message in args.messages or DEFAULT_MESSAGES
    ]
    serve_pydex_commands(args.host, args.port, frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
