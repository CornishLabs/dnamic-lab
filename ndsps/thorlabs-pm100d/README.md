# Thorlabs PM100D NDSP

This is a small host-side ARTIQ controller for a Thorlabs PM100D power meter.
It exposes one SiPyCo RPC target named `powermeter` with these methods:

- `set_autorange(enabled)` enables or disables power autoranging.
- `set_wavelength_nm(wavelength_nm)` sets the sensor calibration wavelength.
- `get_power()` returns one power reading in watts.
- `ping()` checks that the controller process is alive. It does not imply that
  the USB instrument is connected.

The controller starts even when the meter is absent. If it was unplugged
between experiments, the first setup or measurement call discards the stale
VISA handle and tries once to reopen the stable USB resource. A call raises a
descriptive `RuntimeError` while the meter is absent, so an experiment never
mistakes a missing instrument for a zero-watt reading.

Whenever a connection is made, the controller prints the VISA resource, meter
identity, and attached sensor identity. Autorange and wavelength are deliberately
not remembered by the controller: each experiment must set both from whatever
state the meter was left in before calling `get_power()`.

This design assumes the meter remains connected for the duration of an
experiment. Unplugging it during an experiment is unsupported and causes the
current call to report a communication error.

## Local environment

The controller uses a venv contained entirely inside its own `host` directory;
it does not need to change the shared controller-manager or active ARTIQ
environments. Create/update it with:

```bash
cd /home/lab/artiq-files/dnamic-lab/ndsps/thorlabs-pm100d/host
uv sync
```

This installs the host package plus `sipyco`, `pyvisa`, and `pyvisa-py[usb]`
into `host/.venv`. The launch script below uses that local venv by default.

The Linux machine also needs the `libusb` runtime and permission to access the
meter. The udev setup used by the standalone SCPI test remains applicable:

```udev
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="1313", ATTR{idProduct}=="8078", GROUP:="thorlabs", MODE:="0660"
```

## ARTIQ device database

Add this controller entry to `device_db.py` (choose an unused port if `4020` is
already taken):

```python
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
```

That stable resource name binds the controller to PM100D serial `P0011587` and
avoids general VISA resource discovery. The same value is the controller's
default, so the explicit `--resource ...` is optional here; it is shown to make
the hardware binding visible in `device_db.py`. Use a different `--resource`
value to run another PM100D.

## Experiment use

Power-meter calls are host RPC calls. They must be made from host Python, not
from an `@kernel` method: USB/VISA communication is far too slow and
non-deterministic for the RTIO timeline.

```python
from artiq.experiment import EnvExperiment


class ReadPower(EnvExperiment):
    def build(self):
        self.setattr_device("powermeter")

    def run(self):
        # Every experiment establishes all measurement settings it relies on.
        self.powermeter.set_autorange(True)
        self.powermeter.set_wavelength_nm(780.0)
        power_w = self.powermeter.get_power()
        print(f"Power: {power_w:.6g} W")
```

The controller uses a 5 second VISA timeout and averages 1000 meter samples by
default. Both can be changed on the controller command line with `--timeout-ms`
and `--averages`. Run the entry point with `--help` for all options.
