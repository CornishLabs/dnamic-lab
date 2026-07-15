import unittest
from unittest.mock import patch

from dnamic_thorlabs_pm100d_host.driver import (
    DEFAULT_RESOURCE,
    ThorlabsPM100D,
)


RESOURCE = DEFAULT_RESOURCE


class FakeMeter:
    def __init__(self, power="1.25e-6"):
        self.power = power
        self.autorange = "1"
        self.fail_next_write = False
        self.closed = False
        self.writes = []
        self.timeout = None
        self.read_termination = None
        self.write_termination = None

    def write(self, command):
        if self.fail_next_write:
            self.fail_next_write = False
            # This matches the exception produced by PyVISA-py's USBTMC
            # backend when PyUSB reports that a device was disconnected.
            raise ValueError(
                "[Errno 19] No such device (it may have been disconnected)"
            )
        self.writes.append(command)
        if command.startswith("SENS:POW:RANG:AUTO "):
            self.autorange = command.rsplit(" ", 1)[1]

    def query(self, command):
        if command == "*IDN?":
            return "Thorlabs,PM100D,P0011587,3.0.0\n"
        if command == "SYST:SENS:IDN?":
            return "Thorlabs,S120VC,123456\n"
        if command == "SENS:POW:RANG:AUTO?":
            return self.autorange + "\n"
        if command == "SYST:ERR?":
            return '0,"No error"\n'
        if command == "READ?":
            return self.power + "\n"
        raise AssertionError(f"Unexpected query: {command}")

    def close(self):
        self.closed = True


class FakeResourceManager:
    def __init__(self, meters=None):
        self.meters = {name: list(values) for name, values in (meters or {}).items()}
        self.closed = False

    def open_resource(self, resource_name):
        try:
            choices = self.meters[resource_name]
        except KeyError as error:
            raise OSError("resource is absent") from error
        if not choices:
            raise OSError("resource is absent")
        if len(choices) == 1:
            return choices[0]
        return choices.pop(0)

    def close(self):
        self.closed = True


class DriverTests(unittest.TestCase):
    def connect_quietly(self, driver):
        with patch("builtins.print"):
            driver.connect()

    def test_configuration_and_power_reading(self):
        meter = FakeMeter()
        manager = FakeResourceManager({RESOURCE: [meter]})
        driver = ThorlabsPM100D(resource_manager=manager)

        self.connect_quietly(driver)
        self.assertEqual(driver.get_power(), 1.25e-6)
        self.assertIn("CONF:POW", meter.writes)
        self.assertIn("SENS:POW:UNIT W", meter.writes)
        self.assertIn("SENS:AVER:COUN 1000", meter.writes)
        self.assertFalse(
            any(command.startswith("SENS:POW:RANG:AUTO ") for command in meter.writes)
        )
        self.assertFalse(
            any(command.startswith("SENS:CORR:WAV ") for command in meter.writes)
        )

        self.assertFalse(driver.set_autorange(False))
        self.assertEqual(driver.set_wavelength_nm(780), 780.0)
        self.assertIn("SENS:POW:RANG:AUTO 0", meter.writes)
        self.assertIn("SENS:CORR:WAV 780", meter.writes)

    def test_get_power_reports_an_absent_device(self):
        manager = FakeResourceManager()
        driver = ThorlabsPM100D(resource_manager=manager)

        with self.assertRaisesRegex(
            RuntimeError, "Cannot get a power reading: PM100D unavailable"
        ):
            driver.get_power()

        self.assertTrue(driver.ping())

    def test_later_call_connects_after_device_is_plugged_in(self):
        manager = FakeResourceManager()
        driver = ThorlabsPM100D(resource_manager=manager)

        with self.assertRaises(RuntimeError):
            driver.get_power()

        meter = FakeMeter("2.5e-9")
        manager.meters[RESOURCE] = [meter]
        with patch("builtins.print"):
            self.assertEqual(driver.get_power(), 2.5e-9)

    def test_first_setup_call_reopens_a_meter_between_experiments(self):
        first_meter = FakeMeter()
        second_meter = FakeMeter("3e-6")
        manager = FakeResourceManager({RESOURCE: [first_meter, second_meter]})
        driver = ThorlabsPM100D(resource_manager=manager)

        self.connect_quietly(driver)
        self.assertEqual(driver.get_power(), 1.25e-6)

        # The first experiment is over and the meter has been unplugged and
        # reconnected elsewhere. The first setup call of the next experiment
        # encounters the stale handle, reopens it, and then applies its setting.
        first_meter.fail_next_write = True
        with patch("builtins.print"):
            self.assertFalse(driver.set_autorange(False))
        driver.set_wavelength_nm(852.3)

        self.assertTrue(first_meter.closed)
        self.assertIn("SENS:POW:RANG:AUTO 0", second_meter.writes)
        self.assertIn("SENS:CORR:WAV 852.3", second_meter.writes)


if __name__ == "__main__":
    unittest.main()
