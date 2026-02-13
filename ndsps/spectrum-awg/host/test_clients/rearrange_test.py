#!/usr/bin/env python3
from sipyco.pc_rpc import Client
from awgsegmentfactory import AWGProgramBuilder, IntentIR

from time import perf_counter
import time

import random
from typing import List

def random_increasing_4() -> List[int]:
    """Return 4 strictly increasing integers between 0 and 9 inclusive."""
    return sorted(random.sample(range(10), 4))

def build_intent_ir_dict():
    # intent_ir = IntentIR.from_preset("spec_analyser_test")
    intent_ir = IntentIR.from_preset("rt_spec_analyser_rearr_hotswap")
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
        t0=perf_counter()
        c.plan_phase_compile_upload(intent_ir_dict,force=True)
        t1=perf_counter()
        print(f"{(t1-t0)*1e3}ms ")
        print("Sequence compile/upload request sent.")
        time.sleep(6)
        while True:
            t00=perf_counter()
            new_src = random_increasing_4()
            print(f"Hotswapping {new_src=} ...")
            t0=perf_counter()
            c.hotswap_remap_src(src=new_src)
            t1=perf_counter()
            print(f"hotswap took {(t1-t0)*1e3}ms ")
            t11=perf_counter()
            time.sleep(6-220e-6-(t11-t00))

    finally:
        c.close_rpc()


if __name__ == "__main__":
    main()
