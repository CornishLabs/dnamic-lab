import logging
from datetime import datetime, timezone

from sipyco.pc_rpc import Client
from artiq.tools import parse_arguments, parse_devarg_override


def submit_experiment(
    host="localhost",
    port=3251,                 # ARTIQ master (control) default :contentReference[oaicite:2]{index=2}
    pipeline="main",
    file=None,                 # see notes below: repo vs raw filesystem
    class_name=None,
    arguments=(),
    priority=0,
    due_date=None,             # seconds since epoch, or None
    flush=False,
    use_repository=False,
    repo_rev=None,             # None => HEAD
    devarg_override="",
    log_level=logging.WARNING,
):
    expid = {
        "class_name": class_name,
        "arguments": parse_arguments(list(arguments)),
        "devarg_override": parse_devarg_override(devarg_override),
        "log_level": log_level,
    }

    if use_repository:
        # Dashboard-style submission: file is relative to repository root
        expid["file"] = file
        expid["repo_rev"] = repo_rev  # include the key to enable repo backend
    else:
        # Raw filesystem submission: file path must exist on the *master* machine
        expid["file"] = file

    remote = Client(host, port, "schedule")
    try:
        rid = remote.submit(pipeline, expid, priority, due_date, flush)
        return rid
    finally:
        remote.close_rpc()


if __name__ == "__main__":
    # example: run ASAP with priority 10
    # from sipyco import pyon
    # from pprint import pp

    # arguments should be a list arguments: [str].  e.g. [keyone=pyonvalueone, keytwo=pyonvaluetwo]
    voltages_mv = [112.8,110.4,-114.8,-111.7]
    voltages = [v*1e-3 for v in voltages_mv]
    names = ["A","B","C","D"]
    args_str_list = ['='.join([n,f"{v:.6f}"]) for (n,v) in zip(names,voltages)]

    # e.g. ["A=0.0428","B=0.0404","C=-0.0448","D=-0.0417"] 
    # This gets converted into a dictionary that looks like
    # "arguments":{"A":0.0428,"B":0.0404,"C":-0.0448,"D":-0.0417}   Taken from an expid
    # I believe this design decision is because the top looks like command line args.

    # Old NDScan gets one bit argument like:
    # "arguments":{"ndscan_params":"{\"instances\":{\"\":[\"dark_counts.DarkCounts.target_temperature\"]},... ,"repo_rev":"N/A"}

    rid = submit_experiment(
        host="localhost",
        pipeline="main",
        file="repository/simple_examples/hardware_tests/zotino/zotino_live.py",     # adjust depending on mode below
        class_name="SetZotinoVoltages",
        arguments=args_str_list,
        priority=10,
        due_date=None,
        flush=False,
        use_repository=False,
    )
    print("Submitted RID:", rid)
