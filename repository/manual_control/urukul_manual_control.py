from typing import Type

from artiq.coredevice.ad9910 import AD9910
from artiq.coredevice.core import Core
from artiq.language.core import delay, kernel
from artiq.language.environment import (
    BooleanValue,
    EnumerationValue,
    EnvExperiment,
    HasEnvironment,
    NumberValue,
)
from artiq.language.units import MHz, dB, ms


DEFAULT_CHANNEL = "dds_ch_rb_cool"


def _resolve_device(has_env: HasEnvironment, device_name: str):
    device_db = has_env.get_device_db()
    resolved_name = device_name
    description = device_db[resolved_name]
    seen = set()

    while isinstance(description, str):
        if description in seen:
            raise ValueError(f"Device alias loop involving {device_name!r}")
        seen.add(description)
        resolved_name = description
        description = device_db[resolved_name]

    return resolved_name, description


def _description_matches_class(description, class_type: Type) -> bool:
    class_name = class_type.__name__
    module_name = class_type.__module__

    if not (
        isinstance(description, dict)
        and description.get("type") == "local"
        and description.get("module") == module_name
        and description.get("class") == class_name
    ):
        return False

    if class_type is AD9910:
        return "sw_device" in description.get("arguments", {})

    return True


def _get_local_devices(has_env: HasEnvironment, class_type: Type) -> tuple[str, ...]:
    """Return local devices of a given type, with aliases before raw names."""

    device_db = has_env.get_device_db()
    aliases = []
    raw_channels = []

    for device_name in sorted(device_db):
        try:
            resolved_name, description = _resolve_device(has_env, device_name)
        except (KeyError, ValueError):
            continue

        if not _description_matches_class(description, class_type):
            continue

        if resolved_name == device_name:
            raw_channels.append(device_name)
        else:
            aliases.append(device_name)

    return tuple(aliases + raw_channels)


class SetUrukulTone(EnvExperiment):
    """Set one Urukul DDS channel."""

    def build(self):
        self.setattr_device("core")
        self.core: Core

        dds_channels = _get_local_devices(self, AD9910)
        if not dds_channels:
            dds_channels = (DEFAULT_CHANNEL,)
        default_channel = (
            DEFAULT_CHANNEL if DEFAULT_CHANNEL in dds_channels else dds_channels[0]
        )

        self.setattr_argument(
            "urukul_channel",
            EnumerationValue(dds_channels, default=default_channel),
        )
        self.setattr_argument(
            "frequency",
            NumberValue(10 * MHz, unit="MHz", scale=MHz, step=1 * MHz, min=0.0),
        )
        self.setattr_argument(
            "amplitude",
            NumberValue(0.1, step=0.01, min=0.0, max=1.0, precision=3),
        )
        self.setattr_argument(
            "phase",
            NumberValue(
                0.0,
                unit="turns",
                scale=1.0,
                step=0.001,
                min=0.0,
                max=1.0,
                precision=4,
            ),
        )
        self.setattr_argument(
            "attenuation",
            NumberValue(
                8.0 * dB,
                unit="dB",
                scale=dB,
                step=1.0 * dB,
                min=0.0,
                max=31.5 * dB,
            ),
        )
        self.setattr_argument("rf_switch_on", BooleanValue(True))

    def prepare(self):
        self.dds: AD9910 = self.get_device(self.urukul_channel)

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {
            "dds",
            "frequency",
            "amplitude",
            "phase",
            "attenuation",
            "rf_switch_on",
        }

    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()

        self.dds.cpld.init()
        self.core.break_realtime()
        delay(50*ms)
        self.dds.init()
        delay(10 * ms)

        self.dds.cpld.get_att_mu()
        self.core.break_realtime()

        self.dds.set_att(self.attenuation)
        self.dds.set(
            frequency=self.frequency,
            phase=self.phase,
            amplitude=self.amplitude,
        )
        self.dds.sw.set_o(self.rf_switch_on)
