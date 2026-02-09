import threading
from typing import Optional, Tuple

import numpy as np
import spcm

from awgsegmentfactory import (
    AWGProgramBuilder,
    ResolvedIR,
    compile_sequence_program,
    format_samples_time,
    quantize_resolved_ir,
    resolve_intent_ir
)

from awgsegmentfactory import IntentIR
from awgsegmentfactory.debug import format_ir

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


def _setup_spcm_sequence_from_compiled(sequence, compiled) -> None:
    segments_hw = []
    for seg in compiled.segments:
        s = sequence.add_segment(seg.n_samples)
        s[:, :] = seg.data_i16
        segments_hw.append(s)

    steps_hw = []
    for step in compiled.steps:
        steps_hw.append(
            sequence.add_step(segments_hw[step.segment_index], loops=step.loops)
        )

    sequence.entry_step(steps_hw[0])

    for step in compiled.steps:
        steps_hw[step.step_index].set_transition(
            steps_hw[step.next_step], on_trig=step.on_trig
        )

    sequence.write_setup()


class SpectrumAWGCompilerUploader:

    def __init__(self, serial_number: int, simulation: bool = False):
        self.serial_number = int(serial_number)
        self.simulation = bool(simulation)
        
    def ping(self) -> bool:
        return True
    
    def plan_phase_compile_upload(self, intent_ir_dict):
        intent_ir = IntentIR.decode(intent_ir_dict)
        print("Recieved intent IR:")
        print(format_ir(intent_ir))
        print("TODO: implement the rest of the compilation pipeline")

        ####

        # TODO: Make these command line params in the ctl command line param
        sample_rate_hz = 625e6
        logical_channel_to_hardware_channel = {"H": 0, "V": 1}

        ir = resolve_intent_ir(intent_ir, sample_rate_hz=sample_rate_hz)
        q = quantize_resolved_ir(
            ir, logical_channel_to_hardware_channel=logical_channel_to_hardware_channel,
            segment_quantum_s=4e-6
        )

        # If you don't have a card connected, use a safe "typical" int16 full-scale.
        full_scale_default = (2**15)-1 #=32767
        compiled = compile_sequence_program(
            q,
            gain=1.0,
            clip=1.0,
            full_scale=full_scale_default,
        )

        print(f"compiled segments: {len(compiled.segments)} | steps: {len(compiled.steps)}")
        _print_quantization_report(compiled)

        # Optional: upload to a Spectrum card (requires Spectrum driver + spcm Python package).
        try:
            import spcm
            from spcm import units
        except Exception as exc:
            print(f"spcm not available (skipping upload): {exc}")
            return

        try:
            with spcm.Card(card_type=spcm.SPCM_TYPE_AO, verbose=False) as card:
                card.card_mode(spcm.SPC_REP_STD_SEQUENCE)

                # Configure enabled channels (H->CH0, V->CH1)
                channels = spcm.Channels(card, card_enable=spcm.CHANNEL0 | spcm.CHANNEL1)
                channels.enable(True)
                channels.output_load(50*units.ohm)
                channels.amp(450 * units.mV)
                channels.stop_level(spcm.SPCM_STOPLVL_HOLDLAST)

                # Triggers: EXT0 ends wait_trig steps.
                trigger = spcm.Trigger(card)
                trigger.or_mask(spcm.SPC_TMASK_EXT0)
                trigger.ext0_mode(spcm.SPC_TM_POS)
                trigger.ext0_level0(0.5 * units.V)
                trigger.ext0_coupling(spcm.COUPLING_DC)
                trigger.termination(1)
                trigger.delay(0)

                # Sample clock
                clock = spcm.Clock(card)
                clock.mode(spcm.SPC_CM_INTPLL)
                clock.sample_rate(compiled.sample_rate_hz * units.Hz)
                clock.clock_output(False)

                # Compile again with the card's exact DAC scaling.
                full_scale = int(card.max_sample_value()) - 1
                compiled = compile_sequence_program(
                    q,
                    gain=1.0,
                    clip=0.1,
                    full_scale=full_scale,
                )

                sequence = spcm.Sequence(card)
                _setup_spcm_sequence_from_compiled(sequence, compiled)
                print("sequence written; starting card (Ctrl+C to stop)")

                card.timeout(0)
                card.start(spcm.M2CMD_CARD_ENABLETRIGGER, spcm.M2CMD_CARD_FORCETRIGGER)
                try:
                    while True:
                        time.sleep(0.2)
                except KeyboardInterrupt:
                    pass
                finally:
                    card.stop()
        except spcm.SpcmException as exc:
            print(f"Could not open Spectrum card (skipping upload): {exc}")
            return



    def print_card_info(self):
        with spcm.Card(serial_number=self.serial_number) as card:
            product_name = card.product_name()
            status = card.status()
            print(f"Product: {product_name}, card status: {status}")



