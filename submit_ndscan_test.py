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
    from sipyco import pyon
    from pprint import pp
    ndscan_params_str = r"""{"instances": {"": ["load_mot.LoadRbMOT.cool_frequency", "load_mot.LoadRbMOT.repump_frequency", "load_mot.LoadRbMOT.cool_dds_amp", "load_mot.LoadRbMOT.repump_dds_amp", "load_mot.LoadRbMOT.cool_dds_att", "load_mot.LoadRbMOT.repump_dds_att", "load_mot.LoadRbMOT.quad_setpoint", "load_mot.LoadRbMOT.NS_setpoint", "load_mot.LoadRbMOT.EW_setpoint", "load_mot.LoadRbMOT.UD_setpoint", "load_mot.LoadRbMOT.preload_time", "load_mot.LoadRbMOT.exposure_time"]}, "schemata": {"load_mot.LoadRbMOT.cool_frequency": {"fqn": "load_mot.LoadRbMOT.cool_frequency", "description": "Cool light AOM drive frequency", "type": "float", "default": "110000000.0", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": 60000000.0, "max": 160000000.0}}, "load_mot.LoadRbMOT.repump_frequency": {"fqn": "load_mot.LoadRbMOT.repump_frequency", "description": "Repump light AOM drive frequency", "type": "float", "default": "110000000.0", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": 60000000.0, "max": 160000000.0}}, "load_mot.LoadRbMOT.cool_dds_amp": {"fqn": "load_mot.LoadRbMOT.cool_dds_amp", "description": "Cool light AOM DDS amp (0-1)", "type": "float", "default": "0.6", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": 0, "max": 1}}, "load_mot.LoadRbMOT.repump_dds_amp": {"fqn": "load_mot.LoadRbMOT.repump_dds_amp", "description": "Repump light AOM DDS amp (0-1)", "type": "float", "default": "0.6", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": 0, "max": 1}}, "load_mot.LoadRbMOT.cool_dds_att": {"fqn": "load_mot.LoadRbMOT.cool_dds_att", "description": "Cool light AOM DDS attenuator", "type": "float", "default": "3.0", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": 0.0, "max": 30.0}}, "load_mot.LoadRbMOT.repump_dds_att": {"fqn": "load_mot.LoadRbMOT.repump_dds_att", "description": "Repump light AOM DDS attenuator", "type": "float", "default": "3.0", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": 0.0, "max": 30.0}}, "load_mot.LoadRbMOT.quad_setpoint": {"fqn": "load_mot.LoadRbMOT.quad_setpoint", "description": "Quad coil servo setpoint voltage", "type": "float", "default": "8.8", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": 0.0, "max": 10.0}}, "load_mot.LoadRbMOT.NS_setpoint": {"fqn": "load_mot.LoadRbMOT.NS_setpoint", "description": "N/S Shims servo setpoint voltage", "type": "float", "default": "0.8", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": -10.0, "max": 10.0}}, "load_mot.LoadRbMOT.EW_setpoint": {"fqn": "load_mot.LoadRbMOT.EW_setpoint", "description": "E/W Shims servo setpoint voltage", "type": "float", "default": "0.8", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": -10.0, "max": 10.0}}, "load_mot.LoadRbMOT.UD_setpoint": {"fqn": "load_mot.LoadRbMOT.UD_setpoint", "description": "U/D Shims servo setpoint voltage", "type": "float", "default": "0.8", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": -10.0, "max": 10.0}}, "load_mot.LoadRbMOT.preload_time": {"fqn": "load_mot.LoadRbMOT.preload_time", "description": "Time to load MOT before imaging starts", "type": "float", "default": "3.0", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": 0.001, "max": 30.0}}, "load_mot.LoadRbMOT.exposure_time": {"fqn": "load_mot.LoadRbMOT.exposure_time", "description": "Time spent fluorescing while exposing", "type": "float", "default": "1.0", "spec": {"is_scannable": true, "scale": 1, "step": 0.1, "min": 0.001, "max": 30.0}}}, "always_shown": [("load_mot.LoadRbMOT.cool_frequency", ""), ("load_mot.LoadRbMOT.repump_frequency", ""), ("load_mot.LoadRbMOT.cool_dds_amp", ""), ("load_mot.LoadRbMOT.repump_dds_amp", ""), ("load_mot.LoadRbMOT.cool_dds_att", ""), ("load_mot.LoadRbMOT.repump_dds_att", ""), ("load_mot.LoadRbMOT.quad_setpoint", ""), ("load_mot.LoadRbMOT.NS_setpoint", ""), ("load_mot.LoadRbMOT.EW_setpoint", ""), ("load_mot.LoadRbMOT.UD_setpoint", ""), ("load_mot.LoadRbMOT.preload_time", ""), ("load_mot.LoadRbMOT.exposure_time", "")], "overrides": {"load_mot.LoadRbMOT.cool_frequency": [{"path": "", "value": 110000000.0}], "load_mot.LoadRbMOT.repump_frequency": [{"path": "", "value": 110000000.0}], "load_mot.LoadRbMOT.cool_dds_amp": [{"path": "", "value": 0.6}], "load_mot.LoadRbMOT.repump_dds_amp": [{"path": "", "value": 0.6}], "load_mot.LoadRbMOT.cool_dds_att": [{"path": "", "value": 3.0}], "load_mot.LoadRbMOT.repump_dds_att": [{"path": "", "value": 3.0}], "load_mot.LoadRbMOT.quad_setpoint": [{"path": "", "value": 8.8}], "load_mot.LoadRbMOT.NS_setpoint": [{"path": "", "value": 0.8}], "load_mot.LoadRbMOT.EW_setpoint": [{"path": "", "value": -0.367}], "load_mot.LoadRbMOT.UD_setpoint": [{"path": "", "value": -0.119}], "load_mot.LoadRbMOT.preload_time": [{"path": "", "value": 3.0}], "load_mot.LoadRbMOT.exposure_time": [{"path": "", "value": 1.0}]}, "scan": {"axes": [], "num_repeats": 1, "no_axes_mode": "single", "randomise_order_globally": false, "num_repeats_per_point": 1, "skip_on_persistent_transitory_error": false}}"""
    
    ndscan_params_dict = pyon.decode(ndscan_params_str) # Now a python dict
    pp(ndscan_params_dict) # Pretty print it

    encoded_str = pyon.encode(ndscan_params_dict) # Re-encode it
    print(type(encoded_str)) #Now a str again

    rid = submit_experiment(
        host="::1",
        pipeline="main",
        file="repository/sequences/load_mot.py",     # adjust depending on mode below
        class_name="MOTLoadExp",
        arguments=[r"ndscan_params='"+encoded_str+r"'"],
        priority=10,
        due_date=None,
        flush=False,
        use_repository=False,                   # see next section
    )
    print("Submitted RID:", rid)
