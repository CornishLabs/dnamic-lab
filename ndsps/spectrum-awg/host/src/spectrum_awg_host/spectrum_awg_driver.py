import threading
from typing import Optional, Tuple

import logging

import numpy as np

import spcm
from spcm import units

from awgsegmentfactory import (
    QIRtoSamplesSegmentCompiler,
    format_samples_time,
    quantize_resolved_ir,
    resolve_intent_ir,
    upload_sequence_program
)

from awgsegmentfactory import IntentIR
from awgsegmentfactory.debug import format_ir

from .calibration_constants import lut

logger = logging.getLogger(__name__)


def _print_quantization_report(compiled) -> None:
    fs = float(compiled.sample_rate_hz)
    if not compiled.quantization:
        return
    q = compiled.quantization[0].quantum_samples
    step = compiled.quantization[0].step_samples
    print(f"segment quantum: {format_samples_time(q, fs)} | step: {step} samples")
    for qi in compiled.quantization:
        o = format_samples_time(qi.original_samples, fs)
        n = format_samples_time(qi.quantized_samples, fs)
        print(
            f"- {qi.name}: {o} -> {n} | mode={qi.mode} loop={qi.loop} loopable={qi.loopable}"
        )

def _channel_mask(n_ch: int) -> int:
    mask = 0
    for i in range(n_ch):
        mask |= int(getattr(spcm, f"CHANNEL{i}"))
    return mask


class SpectrumAWGCompilerUploader:
    """
    This device manages a connection with the spectrum instruments card, and 
    can compile quantised_ir to int16 samples for the card, and then upload them
    to the card.

    This class is not intrinsically thread-safe; the SiPyCo RPC server
    (see sipyco.pc_rpc.simple_server_loop, sipyco.pc_rpc.Server)
    provides serialisation of calls when `allow_parallel=False` which is the default.
    
    The onus is on the user to ensure a call order that is reasonable.
    """

    def __init__(self, 
                 serial_number: int,
                 sample_rate_hz, 
                 card_max_mv: int, 
                 physical_setup_info_str: str, 
                 simulation: bool = False):
        
        # Card identification
        self.serial_number = int(serial_number)

        # Card settings
        self.sample_rate_hz = sample_rate_hz
        self.card_max_mv = card_max_mv

        # Info on what the card is connected to and how
        try:
            self.physical_setup = lut[physical_setup_info_str]
        except KeyError as exc:
            valid = ", ".join(sorted(lut))
            raise ValueError(
                f"Unknown physical setup {physical_setup_info_str!r}. "
                f"Valid options: {valid}"
            ) from exc

        # Don't touch hardware, just produce debug plots
        self.simulation = bool(simulation)

        # Owned card connection details
        self._card_cm = None   # holds spcm.Card(...) context manager
        self._card = None      # holds the active/entered card handle

        # Caching/managing/storage of current sequence
        self._card_configured = False # Whether or not the outputs are setup as expected
        self.current_seq_hash = None # This will tell us whether or not we need to recompile & upload
        self._upload_session = None # Needed for hotswap

    def _setup_card_once(self, card):
        if self._card_configured:
            return

        card.card_mode(spcm.SPC_REP_STD_SEQUENCE)

        n_ch = self.physical_setup.N_ch
        channels = spcm.Channels(card, card_enable=_channel_mask(n_ch))
        channels.enable(True)
        channels.output_load(50 * units.ohm)
        channels.amp(self.card_max_mv * units.mV)
        channels.stop_level(spcm.SPCM_STOPLVL_HOLDLAST)

        trigger = spcm.Trigger(card)
        trigger.or_mask(spcm.SPC_TMASK_EXT0)
        trigger.ext0_mode(spcm.SPC_TM_POS)
        trigger.ext0_level0(0.8 * units.V)
        trigger.ext0_coupling(spcm.COUPLING_DC)
        trigger.termination(1)
        trigger.delay(0)

        clock = spcm.Clock(card)
        clock.mode(spcm.SPC_CM_INTPLL)
        clock.sample_rate(self.sample_rate_hz * units.Hz)
        clock.clock_output(False)

        self._card_configured = True

    def get_card(self):
        """
        Returns a comms handle to the card, uses existing connection if exists,
        otherwise it creates one.
        """
        if self._card is not None:
            return self._card

        self._card_cm = spcm.Card(
            serial_number=self.serial_number,
            card_type=spcm.SPCM_TYPE_AO,
        )
        self._card = self._card_cm.__enter__()

        # Optional: do one-time configuration here (mode, channels, trigger, clock, etc.)
        return self._card

    def close_card(self):
        """
        Closes the connection to a card, if we have one. This will also stop the card.
        """
        if self._card_cm is None:
            return
        # Ensure hardware is left in a sane state if you want:
        try:
            self._card.stop(spcm.M2CMD_DATA_STOPDMA)
        finally:
            self._card_cm.__exit__(None, None, None)
            self._card_cm = None
            self._card = None
            self._card_configured = False
            self.current_seq_hash = None
            self._upload_session = None
        
    def ping(self) -> bool:
        return True
    
    def plan_phase_compile_upload(self, intent_ir_dict):
        intent_ir = IntentIR.decode(intent_ir_dict)
        logger.info(f"Received intent IR:\n{format_ir(intent_ir)}")

        new_hash = intent_ir.digest()

        if new_hash == self.current_seq_hash:
            logger.info("Intent IR unchanged; skipping compile/upload.")
            return

        ir = resolve_intent_ir(intent_ir, sample_rate_hz=self.sample_rate_hz)
        q = quantize_resolved_ir(ir, segment_quantum_s=40e-6)


        if self.simulation:
            # Compute samples, but don't send to card.
            slots = QIRtoSamplesSegmentCompiler(
                quantised=q,
                physical_setup=self.physical_setup,
                full_scale_mv=self.card_max_mv,
                full_scale=(2**15 - 1),
            )
            slots.compile_to_card_int16()
            self.current_seq_hash = new_hash
            # TODO: make debug plot appear
            return

        card = self.get_card()

        # Stop whatever is currently playing
        card.stop(spcm.M2CMD_DATA_STOPDMA)
        
        self._setup_card_once(card)
        
        # Compile again with the card's exact DAC scaling.
        full_scale = int(card.max_sample_value()) - 1
        slots_compiler = QIRtoSamplesSegmentCompiler(
            quantised=q,
            physical_setup=self.physical_setup,
            full_scale_mv=self.card_max_mv,
            full_scale=full_scale,
        )
        slots_compiler.compile_to_card_int16()

        try:
            self._upload_session = upload_sequence_program(slots_compiler, mode="cpu", card=card)  # keep, don’t reuse yet
            logger.info("Upload to AWG successful, starting")
            card.timeout(0)
            card.start(spcm.M2CMD_CARD_ENABLETRIGGER, spcm.M2CMD_CARD_FORCETRIGGER)
        except Exception:
            self._upload_session = None
            self.current_seq_hash = None
            self._card_configured = False
            try:
                card.stop(spcm.M2CMD_DATA_STOPDMA)
            except Exception:
                pass
            raise
        else:
            self.current_seq_hash = new_hash

    def print_card_info(self):
        card = self.get_card()
        product_name = card.product_name()
        status = card.status()
        print(f"Product: {product_name}, card status: {status}")
