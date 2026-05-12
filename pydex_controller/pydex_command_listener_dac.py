"""PyDex command listener for submitting DAC voltage updates.

This listens for PyDex `set_data=...` commands, updates the requested DAC field
values, and submits the ARTIQ experiment that applies the corresponding Zotino
channel voltages.

See `pydex_controller/README.md` for the PyDex transport architecture and the
reason this command listener is conceptually a receiver but technically a TCP
client.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import logging
from typing import Any, Optional

try:
    from .pydex_transport import poll_pydex_commands
except ImportError:  # Allows running this file directly from this directory.
    from pydex_transport import poll_pydex_commands

# Artiq import
from code_submit import submit_experiment

LOGGER = logging.getLogger("pydex_controller.pydex_command_listener_dac")

############################
PYDEX_HOST = "192.168.23.10"
PYDEX_PORT = 8636
DAC_FIELD_NAMES = {"NEGX_mV", "NEGZ_mV", "COM_mV", "CHECK_mV"}

@dataclass
class DACControllerState:
    out_voltages_v: list[float] = field(
        default_factory=lambda: [0.0, 0.0, 0.0, 0.0]
    )
    zero_output_voltages_v: list[float] = field(
        default_factory=lambda: [0.0, 0.0, 0.0, 0.0]
    )
    NEGX_mV: float = 0
    NEGZ_mV: float = 0  
    COM_mV: float = 0
    CHECK_mV: float = 0
    
    dry_run: bool = True
    artiq_host: str = "localhost"
    pipeline: str = "main"
    file: str = "repository/simple_examples/hardware_tests/zotino/zotino_live.py"
    class_name: str = "SetZotinoVoltages"
    priority: int = 10


def setup(*, dry_run: bool = True) -> DACControllerState:
    """Set up the device-specific controller state.

    In a real implementation this is where you would open hardware handles,
    load calibration/configuration files, or perform any initial device setup.
    """

    LOGGER.info("Running generic setup; dry_run=%s", dry_run)
    return DACControllerState(dry_run=dry_run, 
                              zero_output_voltages_v=[-2.7e-3,-0.3e-3,4.7e-3,1.5e-3]
                              )

def field_values_to_electrode_voltages(state: DACControllerState):
    # ELECTRODE_GEOM (looking from the rear of the electrodes along STIRAP beams)
    #              z(U)
    #   D     C      ^
    #      o         |
    #   A     B      |
    #  x(N)  <-------⊙ y(W) towards you
    # Electric field lines go from positive to negative
    # So for an E field along +x, we want (B,C) positive, and (A,D) negative
    # So for an E field along +z, we want (A,B) positive, and (C,D) negative
    
    # Our spreadsheet we were using in the lab had the 'Horizontal field' and 'Vertical field'
    # along negative x and negative z direction, so we will keep this convention, however explitly state this.

    # We also have a 'checkerboard' and 'common' mode terms.
    # The checkerboard convention, for a positive term A,C >0 ; B,D <0
    # This produces an E-field gradient (TODO: need to work out sign)

    # The user sets:
    #NEGX_mV
    #NEGZ_mV
    #COM_mV
    #CHECK_mV

    #                  H          +      V        +    CHECK          +   COM
    A_bare_mv =  state.NEGX_mV/2  + -state.NEGZ_mV/2 +  state.CHECK_mV/2 +  state.COM_mV
    B_bare_mv = -state.NEGX_mV/2  + -state.NEGZ_mV/2 + -state.CHECK_mV/2 +  state.COM_mV
    C_bare_mv = -state.NEGX_mV/2  +  state.NEGZ_mV/2 +  state.CHECK_mV/2 +  state.COM_mV
    D_bare_mv =  state.NEGX_mV/2  +  state.NEGZ_mV/2 + -state.CHECK_mV/2 +  state.COM_mV

    A_set_V = 1e-3*A_bare_mv - state.zero_output_voltages_v[0]
    B_set_V = 1e-3*B_bare_mv - state.zero_output_voltages_v[1]
    C_set_V = 1e-3*C_bare_mv - state.zero_output_voltages_v[2]
    D_set_V = 1e-3*D_bare_mv - state.zero_output_voltages_v[3]

    state.out_voltages_v = [A_set_V, B_set_V, C_set_V, D_set_V]

def upload_voltages_to_dac(state: DACControllerState)-> int:
    
    voltages = state.out_voltages_v
    if len(voltages) != 4:
        raise ValueError(f"Expected 4 DAC voltages, got {len(voltages)}")

    names = ["A","B","C","D"]
    args_str_list = ['='.join([n,f"{v:.6f}"]) for (n,v) in zip(names,voltages)]

    rid = submit_experiment(
        host=state.artiq_host,
        pipeline=state.pipeline,
        file=state.file,
        class_name=state.class_name,
        arguments=args_str_list,
        priority=state.priority,
        use_repository=False,
    )

    return rid

def parse_dac_updates(payload: Any) -> dict[str, float]:
    if not isinstance(payload, list):
        raise ValueError("set_data payload must be a list")

    updates: dict[str, float] = {}
    for index, update in enumerate(payload):
        if not isinstance(update, (list, tuple)) or len(update) != 2:
            raise ValueError(f"Update #{index} must be [field_name, value]")

        field_name, raw_value = update
        if field_name not in DAC_FIELD_NAMES:
            raise ValueError(f"Unknown DAC field in update #{index}: {field_name!r}")

        try:
            updates[field_name] = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"DAC field {field_name!r} value must be numeric"
            ) from exc

    if not updates:
        raise ValueError("set_data payload contained no updates")
    return updates


def do_something_with_parsed_data(
    state: DACControllerState,
    command: ParsedCommand,
) -> None:
    
    if state.dry_run:
        LOGGER.info("Dry run enabled; leaving hardware unchanged")

    if command.name == "save_all":
        LOGGER.info("Ignoring save_all for DAC listener")
        return
    elif command.name == "set_data":
        LOGGER.info("set_data received")
        LOGGER.info("%s", command.raw_payload)
        
        # Format of command.payload is [["NEGX_mV",34.0],["NEGZ_mV", 50.0]]
        #NEGX_mV
        #NEGZ_mV
        #COM_mV
        #CHECK_mV
        for field_name, value in parse_dac_updates(command.payload).items():
            setattr(state, field_name, value)

        field_values_to_electrode_voltages(state)
        LOGGER.info("Set internal state to:")
        LOGGER.info(f"{state.NEGX_mV=}  {state.NEGZ_mV=}  {state.CHECK_mV=}  {state.COM_mV=}  {state.out_voltages_v}")
        
        if not state.dry_run:
            LOGGER.info("Uploading to DAC...")
            rid = upload_voltages_to_dac(state)
            LOGGER.info(f"Submitted upload experiment rid: {rid}")
    else:
        LOGGER.info(
            "Received command %r with payload %r; no action implemented",
            command.name,
            command.payload,
        )

############################


@dataclass(frozen=True)
class ParsedCommand:
    """A simple parsed representation of a PyDex command string."""

    name: str
    raw_payload: str
    payload: Any

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


def listen_for_commands(args: argparse.Namespace, state: DACControllerState) -> None:
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
