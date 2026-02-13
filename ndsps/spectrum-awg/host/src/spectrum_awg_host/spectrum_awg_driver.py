import threading
from typing import Optional, Tuple, Sequence

import logging

import numpy as np

from dataclasses import replace
from awgsegmentfactory.intent_ir import RemapFromDefOp

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
                 gpu_synth: bool = True,
                 simulation: bool = False):
        
        # Card identification
        self.serial_number = int(serial_number)

        # Card settings
        self.sample_rate_requested_hz = sample_rate_hz
        self.sample_rate_hz = None
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

        self._gpu_synth =  gpu_synth

        # Owned card connection details
        self._card_cm = None   # holds spcm.Card(...) context manager
        self._card = None      # holds the active/entered card handle
        
        self._card_configured = False # Whether or not the outputs are setup as expected
        self._upload_session = None # Needed for hotswap
        self._slots_compiler = None

        # Caching/managing/storage of current sequence
        self.current_seq_hash = None # This will tell us whether or not we need to recompile & upload
        self._last_intent_ir = None
        self._last_quantised = None
        self._segment_index_by_name = {}

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
        self.sample_rate_hz = clock.sample_rate(self.sample_rate_requested_hz * units.Hz)
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

    def close_card(self) -> None:
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
            self.sample_rate_hz = None
            self._last_intent_ir = None
            self._last_quantised = None
            self._slots_compiler = None
            self._segment_index_by_name = {}
        
    def ping(self) -> bool:
        return True
    
    def get_current_step(self) -> int:
        """Returns the current sequence step the card is in."""
        card = self.get_card()
        return card.get_i(spcm.SPC_SEQMODE_STATUS)
    
    def stop_start_card(self) -> None:
        """Restart the card, this will reset to segment zero (takes ~1ms)"""
        card = self.get_card()
        card.stop()
        card.start(spcm.M2CMD_CARD_ENABLETRIGGER, spcm.M2CMD_CARD_FORCETRIGGER) 
    
    def plan_phase_compile_upload(self, intent_ir_dict, force=False):
        intent_ir = IntentIR.decode(intent_ir_dict)
        logger.info(f"Received intent IR:\n{format_ir(intent_ir)}")

        new_hash = intent_ir.digest()

        if (not force) and (new_hash == self.current_seq_hash):
            logger.info("Intent IR unchanged; skipping compile/upload.")
            return


        if self.simulation:
            ir = resolve_intent_ir(intent_ir, sample_rate_hz=self.sample_rate_requested_hz)
            q = quantize_resolved_ir(ir, segment_quantum_s=40e-6)
            # Compute samples, but don't send to card.
            slots = QIRtoSamplesSegmentCompiler(
                quantised=q,
                physical_setup=self.physical_setup,
                full_scale_mv=self.card_max_mv,
                full_scale=(2**15 - 1),
            )
            slots.compile_to_card_int16(gpu=self._gpu_synth, output="numpy")
            self.current_seq_hash = new_hash
            # TODO: make debug plot appear
            return

        card = self.get_card()

        # Stop whatever is currently playing
        card.stop(spcm.M2CMD_DATA_STOPDMA)
        
        self._setup_card_once(card)

        ir = resolve_intent_ir(intent_ir, sample_rate_hz=self.sample_rate_hz)
        q = quantize_resolved_ir(ir, segment_quantum_s=40e-6)
        
        # Compile again with the card's exact DAC scaling.
        full_scale = int(card.max_sample_value()) - 1
        slots_compiler = QIRtoSamplesSegmentCompiler(
            quantised=q,
            physical_setup=self.physical_setup,
            full_scale_mv=self.card_max_mv,
            full_scale=full_scale,
        )
        slots_compiler.compile_to_card_int16(gpu=self._gpu_synth, output="numpy")

        try:
            self._upload_session = upload_sequence_program(slots_compiler, mode="cpu", card=card)  # keep, don’t reuse yet
            logger.info("Upload to AWG successful, starting")
            card.timeout(0)
            card.start(spcm.M2CMD_CARD_ENABLETRIGGER, spcm.M2CMD_CARD_FORCETRIGGER) 
            # A force trigger is needed to start properly for some reason.
        except Exception:
            self._upload_session = None
            self.current_seq_hash = None
            self._card_configured = False
            self._last_intent_ir = None
            self._last_quantised = None
            self._slots_compiler = None
            self._segment_index_by_name = {}
            try:
                card.stop(spcm.M2CMD_DATA_STOPDMA)
            except Exception:
                pass
            raise
        else:
            self.current_seq_hash = new_hash
            self._last_intent_ir = intent_ir
            self._last_quantised = q
            self._slots_compiler = slots_compiler
            self._segment_index_by_name = {
                str(seg.name): i for i, seg in enumerate(q.resolved_ir.segments)
            }
    
    def _patch_intent_remap_src(
        self,
        *,
        intent_ir: IntentIR,
        segment_name: str,
        logical_channel: str,
        src: Sequence[int],
    ) -> IntentIR:
        src_t = tuple(int(i) for i in src)

        seg_idx = next(
            (i for i, s in enumerate(intent_ir.segments) if s.name == segment_name),
            None,
        )
        if seg_idx is None:
            raise ValueError(f"Segment {segment_name!r} not found")

        seg = intent_ir.segments[seg_idx]
        op_idx = next(
            (
                i
                for i, op in enumerate(seg.ops)
                if isinstance(op, RemapFromDefOp) and op.logical_channel == logical_channel
            ),
            None,
        )
        if op_idx is None:
            raise ValueError(
                f"No RemapFromDefOp in segment {segment_name!r} for logical_channel={logical_channel!r}"
            )

        old_op = seg.ops[op_idx]
        if len(src_t) != len(old_op.dst):
            raise ValueError(
                f"src length must match fixed dst length {len(old_op.dst)} (got {len(src_t)})"
            )

        new_ops = list(seg.ops)
        new_ops[op_idx] = replace(old_op, src=src_t)

        new_segments = list(intent_ir.segments)
        new_segments[seg_idx] = replace(seg, ops=tuple(new_ops))

        return replace(intent_ir, segments=tuple(new_segments))



    def hotswap_remap_src(
        self,
        *,
        segment_name: str = "hotswap_rearrange_to_exp_array",
        logical_channel: str = "H",
        src: Sequence[int],
    ) -> None:
        if self.simulation:
            return
        if self._upload_session is None:
            raise RuntimeError("No upload session; run plan_phase_compile_upload once first")
        if self._last_intent_ir is None:
            raise RuntimeError("No cached IntentIR; run plan_phase_compile_upload once first")
        if self._slots_compiler is None:
            raise RuntimeError("No phase seed compiler; run plan_phase_compile_upload first")

        card = self.get_card()

        # 1) Patch intent op
        new_intent = self._patch_intent_remap_src(
            intent_ir=self._last_intent_ir,
            segment_name=segment_name,
            logical_channel=logical_channel,
            src=src,
        )

        # Re-resolve and re-quantize (simple + correct)
        ir = resolve_intent_ir(new_intent, sample_rate_hz=float(self.sample_rate_hz))
        q = quantize_resolved_ir(ir, segment_quantum_s=40e-6)

        # Resolve target segment index
        try:
            seg_idx = int(self._segment_index_by_name[segment_name])
        except KeyError as exc:
            raise ValueError(f"Unknown segment name {segment_name!r}") from exc

        # Compile only that segment, using prior compiled slots as phase seed
        full_scale = int(card.max_sample_value()) - 1
        repo = QIRtoSamplesSegmentCompiler(
            quantised=q,
            physical_setup=self.physical_setup,
            full_scale_mv=self.card_max_mv,
            full_scale=full_scale,
        )
        repo.compile_to_card_int16(
            segment_indices=[seg_idx],
            phase_seed=self._slots_compiler,
            require_phase_seed_for_continue=True,
            gpu=self._gpu_synth,
            output="numpy",
        )

        # Upload only that segment (step graph unchanged)
        self._upload_session = upload_sequence_program(
            repo,
            mode="cpu",
            card=card,
            cpu_session=self._upload_session,
            segment_indices=[seg_idx],
            upload_steps=False,
        )

        # Promote new state
        self._last_intent_ir = new_intent
        self._last_quantised = q
        self.current_seq_hash = new_intent.digest()

    def print_card_info(self):
        card = self.get_card()
        product_name = card.product_name()
        status = card.status()
        print(f"Product: {product_name}, card status: {status}")
    