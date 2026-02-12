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

    p.add_argument("--serial-number", type=int, required=True)
    p.add_argument("--sample-rate-hz", type=int, required=True)
    p.add_argument("--card-max-mv", type=int, default=282)
    p.add_argument("--characterisation-lookup-str", type=str, required=True)
    p.add_argument("--simulation", action="store_true")

    return p


def main():
    args = get_argparser().parse_args()
    sca.init_logger_from_args(args)

    logger.info(f"Starting AWG NDSP for SN {args.serial_number}")
    
    awg = SpectrumAWGCompilerUploader(
        serial_number=args.serial_number,
        sample_rate_hz=args.sample_rate_hz,
        card_max_mv=args.card_max_mv,
        physical_setup_info_str=args.characterisation_lookup_str,
        simulation=args.simulation,
    )
    
    try:
        # Expose ONE target named "awg"
        simple_server_loop(
            {"awg": awg},
            sca.bind_address_from_args(args),
            args.port,
            description="Spectrum AWG NDSP",
            allow_parallel=False
        )
    except KeyboardInterrupt:
        pass
    finally:
        logger.info(f"Ending AWG NDSP for SN {args.serial_number}")
        awg.close_card()

if __name__ == "__main__":
    sys.exit(main())
