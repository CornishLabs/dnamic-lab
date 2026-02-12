#!/usr/bin/env python3
from sipyco.pc_rpc import Client
from awgsegmentfactory import AWGProgramBuilder


def build_intent_ir_dict():
    b = (
        AWGProgramBuilder()
        .logical_channel("H")
        # Uncalibrated channel: amp is mV
        .define("tone_H", logical_channel="H", freqs=[100e6], amps=[300.0], phases="auto")
    )

    b.segment("wait_start", mode="wait_trig")
    b.tones("H").use_def("tone_H")
    b.hold(time=100e-6)

    b.segment("chirp", mode="once")
    b.tones("H").move(df=+1e6, time=300e-6, idxs=[0], kind="linear")

    intent_ir = b.build_intent_ir()
    return intent_ir.encode()  # send dict over RPC


def main():
    host = "127.0.0.1"
    port = 4015
    target = "awg"

    c = Client(host, port, target)
    try:
        assert c.ping() is True
        c.print_card_info()

        intent_ir_dict = build_intent_ir_dict()
        c.plan_phase_compile_upload(intent_ir_dict)

        print("Sequence compile/upload request sent.")
    finally:
        c.close_rpc()


if __name__ == "__main__":
    main()
