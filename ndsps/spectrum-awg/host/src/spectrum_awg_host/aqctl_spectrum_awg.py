#!/usr/bin/env python3
import argparse
import logging
import sys

import sipyco.common_args as sca
from sipyco.pc_rpc import simple_server_loop

from .spectrum_awg_driver import SpectrumAWGCompilerUploader

logger = logging.getLogger(__name__)


def get_argparser():
    p = argparse.ArgumentParser(description="Spectrum AWG Sequence mode controller")
    sca.simple_network_args(p, 4015) #TODO: Check port
    sca.verbosity_args(p)

    p.add_argument("--simulation", action="store_true")
    p.add_argument("--serial-number", action="store_true")

    return p


def main():
    args = get_argparser().parse_args()
    sca.init_logger_from_args(args)

    logger.info("Starting AWG NDSP")
    
    awg = SpectrumAWGCompilerUploader(args.serial_number, simulation=args.simulation)
    
    try:
        # Expose ONE target named "awg"
        simple_server_loop({"awg": awg}, sca.bind_address_from_args(args), args.port)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Ending AWG NDSP")


if __name__ == "__main__":
    sys.exit(main())
