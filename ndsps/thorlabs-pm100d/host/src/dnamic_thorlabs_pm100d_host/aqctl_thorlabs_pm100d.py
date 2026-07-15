#!/usr/bin/env python3

import argparse
import logging
import sys

import sipyco.common_args as sca
from sipyco.pc_rpc import simple_server_loop

from .driver import DEFAULT_RESOURCE, ThorlabsPM100D

logger = logging.getLogger(__name__)


def get_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Thorlabs PM100D power-meter controller"
    )
    sca.simple_network_args(parser, 4020)
    sca.verbosity_args(parser)

    parser.add_argument(
        "--resource",
        default=DEFAULT_RESOURCE,
        help=("exact VISA resource name (default: %(default)s)"),
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=5_000,
        help="VISA communication timeout in milliseconds (default: %(default)s)",
    )
    parser.add_argument(
        "--averages",
        type=int,
        default=1_000,
        help="meter samples averaged per reading (default: %(default)s)",
    )
    return parser


def main() -> None:
    args = get_argparser().parse_args()
    sca.init_logger_from_args(args)

    meter = ThorlabsPM100D(
        resource=args.resource,
        timeout_ms=args.timeout_ms,
        averages=args.averages,
    )

    # A missing bench instrument is an expected condition, not a reason to kill
    # the RPC server. A later call will reopen it after it is plugged in.
    try:
        meter.connect()
    except RuntimeError as error:
        logger.warning("Starting PM100D controller without a meter: %s", error)

    try:
        simple_server_loop(
            {"powermeter": meter},
            sca.bind_address_from_args(args),
            args.port,
            description="Thorlabs PM100D power-meter NDSP",
            allow_parallel=False,
        )
    except KeyboardInterrupt:
        pass
    finally:
        meter.close()


if __name__ == "__main__":
    sys.exit(main())
