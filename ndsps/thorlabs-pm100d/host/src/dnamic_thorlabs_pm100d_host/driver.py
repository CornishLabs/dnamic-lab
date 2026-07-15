from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any, TypeVar

import pyvisa
from pyvisa.errors import VisaIOError

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# A VISA USB name contains stable instrument identifiers, not the temporary
# /dev/bus/usb bus/address assigned by Linux. It therefore remains valid after
# unplugging and reconnecting this particular meter.
DEFAULT_RESOURCE = "USB0::4883::32888::P0011587::0::INSTR"


class ThorlabsPM100D:
    """Small PyVISA wrapper intended to be exposed as a SiPyCo RPC target.

    SiPyCo serializes calls when ``allow_parallel=False``. The controller uses
    that default deliberately, so this class does not need a second lock around
    the VISA resource (most VISA instrument handles are not thread-safe).
    """

    def __init__(
        self,
        *,
        resource: str = DEFAULT_RESOURCE,
        timeout_ms: int = 5_000,
        averages: int = 1_000,
        resource_manager: Any | None = None,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if averages <= 0:
            raise ValueError("averages must be positive")

        self.resource = resource.strip()
        if not self.resource:
            raise ValueError("resource must not be empty")
        self.timeout_ms = int(timeout_ms)
        self.averages = int(averages)

        # Keeping the ResourceManager open is cheap, and it can open the same
        # stable resource name after the USB device is plugged in again.
        self._resource_manager = (
            resource_manager
            if resource_manager is not None
            else pyvisa.ResourceManager("@py")
        )
        self._meter = None

    # ------------------------- RPC-facing API -------------------------

    def ping(self) -> bool:
        """Return True when the RPC controller is alive.

        This intentionally does not claim that the USB meter is connected.
        Hardware availability is checked by setup and measurement calls.
        """

        return True

    def connect(self) -> bool:
        """Connect now, raising RuntimeError if no usable PM100D is available."""

        self._ensure_connected()
        return True

    def set_autorange(self, enabled: bool) -> bool:
        """Enable or disable automatic power-range selection."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be True or False")

        self._with_reconnect(
            "set autoranging",
            lambda meter: self._set_autorange_on_meter(meter, enabled),
        )
        return enabled

    def set_wavelength_nm(self, wavelength_nm: float) -> float:
        """Set the sensor calibration wavelength, in nanometres.

        The PM100D validates the exact supported interval because that interval
        depends on the attached sensor. Here we only reject values that can
        never be meaningful (non-finite or non-positive numbers).
        """

        wavelength_nm = self._validate_wavelength(wavelength_nm)
        self._with_reconnect(
            "set the wavelength",
            lambda meter: self._set_wavelength_on_meter(meter, wavelength_nm),
        )
        return wavelength_nm

    def get_power(self) -> float:
        """Return one power measurement in watts.

        Missing hardware is reported as an exception, never as a numeric
        sentinel. That makes it impossible for an experiment to accidentally
        interpret an unplugged meter as a genuine zero-power result.
        """

        def read(meter) -> float:
            response = meter.query("READ?").strip()
            try:
                return float(response)
            except ValueError as error:
                raise RuntimeError(
                    f"Unexpected response to READ?: {response!r}"
                ) from error

        return self._with_reconnect("get a power reading", read)

    def close(self) -> None:
        """Close the instrument and ResourceManager owned by this driver."""

        self._disconnect()
        try:
            self._resource_manager.close()
        except Exception:
            # Cleanup should not hide the original reason a controller exits.
            logger.debug("Error while closing VISA ResourceManager", exc_info=True)

    # ---------------------- connection management ----------------------

    @staticmethod
    def _validate_wavelength(value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("wavelength_nm must be a positive, finite number")
        return value

    def _ensure_connected(self):
        if self._meter is not None:
            return self._meter

        meter = None
        try:
            # Open the known resource directly. Besides being simpler, this
            # avoids scanning unrelated USB, TCP/IP, serial, and GPIB devices.
            meter = self._resource_manager.open_resource(self.resource)
            meter.timeout = self.timeout_ms
            meter.write_termination = "\n"
            meter.read_termination = "\n"

            identity = meter.query("*IDN?").strip()
            if "THORLABS" not in identity.upper() or "PM100D" not in identity.upper():
                raise RuntimeError(
                    f"resource identified itself as {identity!r}, not a Thorlabs PM100D"
                )

            self._configure_meter(meter)
            sensor = self._read_sensor_information(meter)

        except (VisaIOError, OSError, RuntimeError, ValueError) as error:
            if meter is not None:
                try:
                    meter.close()
                except Exception:
                    pass
            raise RuntimeError(
                f"PM100D unavailable at {self.resource!r}: {error}. Check "
                "that the meter is plugged in and that udev permissions allow "
                "this user to access it."
            ) from error

        self._meter = meter
        self._print_device_information(self.resource, identity, sensor)
        return meter

    def _disconnect(self) -> None:
        meter = self._meter
        self._meter = None
        if meter is not None:
            try:
                meter.close()
            except Exception:
                logger.debug("Error while closing PM100D", exc_info=True)

    def _with_reconnect(
        self,
        action: str,
        operation: Callable[[Any], _T],
    ) -> _T:
        """Run one operation, replacing a stale USB handle once if necessary."""

        for attempt in range(2):
            try:
                meter = self._ensure_connected()
            except RuntimeError as error:
                raise RuntimeError(f"Cannot {action}: {error}") from error

            try:
                return operation(meter)
            except (VisaIOError, OSError, ValueError) as error:
                # After a USB unplug, the old PyVISA object cannot become valid
                # again. Closing it before reopening is what makes hot-plug
                # recovery reliable instead of repeatedly using a stale handle.
                #
                # PyVISA-py's USBTMC backend catches PyUSB's USBError and
                # re-raises it as ValueError. Although ValueError is unusual
                # for a transport failure, it is therefore part of the
                # communication-error API we see here.
                self._disconnect()
                if attempt == 0:
                    logger.warning(
                        "PM100D communication failed while trying to %s; "
                        "attempting one reconnect: %s",
                        action,
                        error,
                    )
                    continue
                raise RuntimeError(
                    f"Cannot {action}: PM100D communication failed after "
                    f"reconnecting ({error})."
                ) from error

        raise AssertionError("unreachable")

    # -------------------------- SCPI helpers --------------------------

    def _configure_meter(self, meter) -> None:
        """Apply fixed controller details to a newly connected meter.

        Autoranging and wavelength are intentionally absent here: experiments
        own those settings and must set them explicitly before measuring.
        """

        meter.write("*CLS")
        meter.write("CONF:POW")
        meter.write("SENS:POW:UNIT W")
        meter.write(f"SENS:AVER:COUN {self.averages}")
        self._check_instrument_errors(meter)

    def _set_autorange_on_meter(self, meter, enabled: bool) -> None:
        meter.write(f"SENS:POW:RANG:AUTO {int(enabled)}")
        response = meter.query("SENS:POW:RANG:AUTO?").strip()
        expected = str(int(enabled))
        if response != expected:
            raise RuntimeError(
                "The PM100D did not accept the requested autorange state; "
                f"expected {expected!r}, received {response!r}."
            )
        self._check_instrument_errors(meter)

    def _set_wavelength_on_meter(self, meter, wavelength_nm: float) -> None:
        meter.write(f"SENS:CORR:WAV {wavelength_nm:.12g}")
        self._check_instrument_errors(meter)

    @staticmethod
    def _check_instrument_errors(meter) -> None:
        """Drain the SCPI error queue and report every queued error together."""

        errors: list[str] = []
        while True:
            response = meter.query("SYST:ERR?").strip()
            code_text, separator, _message = response.partition(",")
            if not separator:
                raise RuntimeError(f"Unexpected response to SYST:ERR?: {response!r}")

            try:
                code = int(code_text)
            except ValueError as error:
                raise RuntimeError(
                    f"Could not parse response to SYST:ERR?: {response!r}"
                ) from error

            if code == 0:
                break
            errors.append(response)

        if errors:
            raise RuntimeError(
                "PM100D reported one or more SCPI errors: " + "; ".join(errors)
            )

    @staticmethod
    def _read_sensor_information(meter) -> str:
        try:
            return meter.query("SYST:SENS:IDN?").strip()
        except (VisaIOError, OSError) as error:
            # Some sensor/firmware combinations do not implement this optional
            # query. It should not prevent ordinary power measurements.
            return f"unavailable ({error})"

    @staticmethod
    def _print_device_information(
        resource_name: str,
        identity: str,
        sensor: str,
    ) -> None:
        print(
            "Connected to Thorlabs PM100D\n"
            f"  Resource:  {resource_name}\n"
            f"  Meter:     {identity}\n"
            f"  Sensor:    {sensor}",
            flush=True,
        )
