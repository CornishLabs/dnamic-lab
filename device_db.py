from device_db_generated import device_db as ddb_gen
from aliases import aliases

custom_controllers = {
    "powermeter": {
        "type": "controller",
        "host": "localhost",
        "port": 4020,
        "target_name": "powermeter",
        "command": (
            "/home/lab/artiq-files/dnamic-lab/ndsps/thorlabs-pm100d/host/"
            "run_aqctl_thorlabs_pm100d -v -p {port} --bind {bind} "
            "--resource USB0::4883::32888::P0011587::0::INSTR"
        ),
    },
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
#     "AWGTest4Ch": {
#         "type": "controller",
#         "host": "localhost",
#         "port": 4015,
#         "target_name": "awg",
#         "command": "/home/lab/artiq-files/dnamic-lab/ndsps/spectrum-awg/host/run_aqctl_spectrum_awg --serial-number 14926 --characterisation-lookup-str AWG_817_CALIB --sample-rate 625000000 --gpu  -vv -p {port} --bind {bind}",
#     },
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
                print(f"Patching SUServo CPLD: {device_name} to be CPLD_alt")
                device_config["class"] = "CPLD_alt"
                device_config["module"] = "repository.lib.suservo_workaround"

        # ARTIQ 9 workaround for SUServo SharedDDS. SharedDDS internally creates
        # a hidden AD9910 with SyncDataUser, while ordinary Urukul AD9910s often
        # have SyncDataEeprom. The compiler can merge those host-object types and
        # fail on the different sync_data attribute type. SharedDDS_alt uses an
        # AD9910_alt inner DDS so the two cases stay distinct. This should be
        # fixed in ARTIQ 10; remove this patch when we move to that.
        if (
            device_config["class"] == "SharedDDS"
            and device_config["module"] == "artiq.coredevice.suservo"
        ):
            print(f"Patching SUServo SharedDDS: {device_name} to be SharedDDS_alt")
            device_config["class"] = "SharedDDS_alt"
            device_config["module"] = "repository.lib.suservo_workaround"

    except KeyError:
        pass
# """


device_db = (ddb_gen | aliases | custom_controllers) # (modified)
