#!/usr/bin/env python3
import argparse
import logging
import sys

import sipyco.common_args as sca
from sipyco.pc_rpc import simple_server_loop

from andor_emccd_driver import AndorEMCCD

logger = logging.getLogger(__name__)


def get_argparser():
    p = argparse.ArgumentParser(description="Andor EMCCD controller (NDSP skeleton)")
    sca.simple_network_args(p, 4010)
    sca.verbosity_args(p)

    p.add_argument("--simulation", action="store_true")
    p.add_argument("--config-path", default="/usr/local/etc/andor")
    p.add_argument("--temp", type=int, default=-50)

    # Trigger mode as int so you can pass Andor enum values directly for now.
    p.add_argument("--trigger-mode", type=int, default=None,
                   help="Andor trigger mode integer (e.g. INTERNAL/EXTERNAL/EXTERNAL_EXPOSURE etc.)")

    return p


def main():
    args = get_argparser().parse_args()
    sca.init_logger_from_args(args)

    cam = AndorEMCCD(config_path=args.config_path, simulation=args.simulation)
    cam.connect()
    logger.info("Connected to camera. serial=%s sim=%s", cam.get_serial() if not args.simulation else "SIM", args.simulation)

    # Typical cold operation:
    cam.cooler_on()
    cam.set_temperature(args.temp)

    # Common defaults; adjust as needed:
    # cam.set_frame_transfer_mode(True)
    # cam.set_shutter(...)

    if args.trigger_mode is not None:
        cam.set_trigger_mode(args.trigger_mode)

    try:
        # Expose ONE target named "camera"
        simple_server_loop({"camera": cam}, sca.bind_address_from_args(args), args.port)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down camera...")
        cam.close()


if __name__ == "__main__":
    sys.exit(main())
