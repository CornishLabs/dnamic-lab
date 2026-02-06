from device_db_generated import device_db as ddb_gen
from aliases import aliases

from dax.sim import enable_dax_sim as maybe_enable_dax_sim

custom_controllers = {
    "andor_ctrl": {
        "type": "controller",
        "host": "::1",
        "port": 4010,
        "target_name": "camera",
        "command": "/home/lab/artiq-files/dnamic-lab/ndsps/andor-camera/host/run_aqctl_andor_emccd -v -p {port} --bind {bind}",
    },
    "andor": {
        "type": "local",
        "module": "dnamic_andor_artiq.mediator",
        "class": "AndorCamera",
        "arguments": {
            "camera": "andor_ctrl",
            "exposure_ttl": "ttl0",
            "core": "core",
        },
    },
    "AWGTest4Ch": {
        "type": "controller",
        "host": "::1",
        "port": 4015,
        "target_name": "awg",
        "command": "/home/lab/artiq-files/dnamic-lab/ndsps/spectrum-awg/host/run_aqctl_spectrum_awg --serial-number 14926 -vv -p {port} --bind {bind}",
    },
}

# Configuration of sim is set in .dax
device_db = maybe_enable_dax_sim((ddb_gen | aliases | custom_controllers), enable=True)