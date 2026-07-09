from device_db_generated import device_db as ddb_gen
from aliases import aliases

custom_controllers = {
    "andor_ctrl": {
        "type": "controller",
        "host": "localhost",
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
            "exposure_ttl": "ttl_camera_exposure",
            "core": "core",
        },
    },
    "AWGTest4Ch": {
        "type": "controller",
        "host": "localhost",
        "port": 4015,
        "target_name": "awg",
        "command": "/home/lab/artiq-files/dnamic-lab/ndsps/spectrum-awg/host/run_aqctl_spectrum_awg --serial-number 14926 --characterisation-lookup-str AWG_817_CALIB --sample-rate 625000000 --gpu  -vv -p {port} --bind {bind}",
    },
}

# Configuration of sim is set in .dax
# device_db_to_mod = ddb_gen 
# """
for device_name, device_config in ddb_gen.items():
    try:
        # Patch any CPLD devices which don't have "io_update_device" i.e. an SUServo'd DDS
        #  devices to be an alternative class (with the same features)
        if (
            device_config["class"] == "CPLD"
            and device_config["module"] == "artiq.coredevice.urukul"
        ):
            if "io_update_device" not in device_config["arguments"]:
                print("Patching SUServo CPLD: %s to be a CPLD_alt", device_name)
                device_config["class"] = "CPLD_alt"
                device_config["module"] = "repository.lib.suservo_workaround"

        # Patch any AD9910 devices which don't have "sw_device" devices to be
        # an alternative class (with the same features)
        # if (
        #     device_config["class"] == "AD9910"
        #     and device_config["module"] == "artiq.coredevice.ad9910"
        # ):
        #     if not "sw_device" in device_config["arguments"]:
        #         print("Patching %s to be a PyAION AD9910_alt", device_name)
        #         device_config["class"] = "AD9910_alt"
        #         device_config["module"] = "repository.lib.suservo_workaround"

    except KeyError:
        pass
# """

"""
device_db["urukul0_cpld"] = {
    "type": "local",
    "module": "artiq.coredevice.urukul",
    "class": "CPLD",
    "arguments": {
        "spi_device": "spi_urukul0",
        "sync_device": "ttl_urukul0_sync",
        "io_update_device": "ttl_urukul0_io_update",
        "refclk": 125000000.0,
        "clk_sel": 2,
        "clk_div": 0,
        "proto_rev": 9
    }
}

VS for an SUServo

device_db["urukul2_cpld"] = {
    "type": "local",
    "module": "artiq.coredevice.urukul",
    "class": "CPLD",
    "arguments": {
        "spi_device": "spi_urukul2",
        "sync_device": None,
        "refclk": 125000000.0,
        "clk_sel": 2,
        "clk_div": 0,
        "proto_rev": 9
    }
}
"""

device_db = (ddb_gen | aliases | custom_controllers) # (modified)