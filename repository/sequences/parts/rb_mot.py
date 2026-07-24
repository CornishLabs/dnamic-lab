"""Reusable Rb MOT-to-tweezers settings, stages, and operations.

This module deliberately contains no complete experiment or result analysis.  A shot
passes the globally owned ``LabRTIOHardware`` instance to the parts here as a non-owning
reference.  Stage settings remain separate and independently scannable even though the
stages act on the same physical devices.

Runtime convention
------------------

* A parameter-only ``*Settings`` fragment declares values but performs no action.
  The stage containing it must consume every one of those values.
* A stage's ``run(...)`` method performs the complete timed stage, including its own
  ``duration``. A composition chooses ordering but never delays on behalf of a child.
  RTIO outputs remain latched afterwards; returning from ``run(...)`` does not restore
  the previous hardware state.
* Boolean arguments describe non-scannable composition choices, such as whether the
  resonant light must be turned on at entry or off at exit. Call sites spell these out
  by keyword so the relevant hardware state is visible in the higher-order sequence.
* State contracts use ``Requires``, ``During`` and ``Leaves`` sections. Directional
  method names are reserved for cases which cannot be expressed clearly as options.
* Stages use a shared hardware helper for calibrated, timed, atomic or coordinated
  operations. A transparent one-device action remains visible at the stage, for
  example ``hardware.ttl_camera_exposure.on()``.
"""

from dataclasses import dataclass

from artiq.experiment import kernel
from artiq.language.core import delay
from artiq.language.units import MHz, V, ms, s

from ndscan.define.fragment import Fragment
from ndscan.define.parameters import FloatParam, FloatParamHandle

from .imaging import CAMERA_EXPOSURE_TIME
from .lab_hardware import UsesLabRTIOHardware

# -----------------------------------------------------------------------------
# Experiment defaults
# -----------------------------------------------------------------------------

TWEEZER_MOT_SETPOINT = 1.15
TWEEZER_IMAGING_SETPOINT = 1.15

SHUTTER_PREFIRE = 10.0 * ms
MOT_HOLD_TIME = 1.0 * s
MOLASSES_TIME = 30.0 * ms
COOLING_TIME = 10.0 * ms


@dataclass(frozen=True)
class LightDefaults:
    """Build-time defaults for one independently scannable light profile."""

    cool_frequency: float
    repump_frequency: float
    cool_amplitude: float
    repump_amplitude: float


@dataclass(frozen=True)
class ShimDefaults:
    """Build-time defaults for the three low-field shim outputs."""

    ew: float
    ud: float
    ns: float


MOT_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=101.25 * MHz,
    repump_frequency=80.64 * MHz,
    cool_amplitude=0.48,
    repump_amplitude=0.32,
)
MOT_SHIM_DEFAULTS = ShimDefaults(ew=-0.367 * V, ud=0.8 * V, ns=-0.112 * V)
MOT_QUAD_DEFAULT = 8.8 * V

MOLASSES_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=136.16 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.67,
    repump_amplitude=0.28,
)
MOLASSES_SHIM_DEFAULTS = ShimDefaults(ew=-0.12 * V, ud=1.15 * V, ns=0.55 * V)

COOLING_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=125.54 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.43,
    repump_amplitude=0.2,
)
COOLING_SHIM_DEFAULTS = ShimDefaults(ew=-0.05 * V, ud=1.1 * V, ns=0.1 * V)

IMAGING_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=103.49 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.415,
    repump_amplitude=0.12,
)


# -----------------------------------------------------------------------------
# Parameter-only profiles
# -----------------------------------------------------------------------------


class _LightSettings(Fragment):
    """The independently scannable light settings for one stage."""

    DEFAULTS: LightDefaults

    def build_fragment(self):
        defaults = self.DEFAULTS
        self.cool_frequency = self.setattr_param(
            "cool_frequency",
            FloatParam,
            "Cool light AOM drive frequency",
            defaults.cool_frequency,
            min=60.0 * MHz,
            max=160.0 * MHz,
        )
        self.cool_frequency: FloatParamHandle

        self.repump_frequency = self.setattr_param(
            "repump_frequency",
            FloatParam,
            "Repump light AOM drive frequency",
            defaults.repump_frequency,
            min=30.0 * MHz,
            max=130.0 * MHz,
        )
        self.repump_frequency: FloatParamHandle

        self.cool_dds_amp = self.setattr_param(
            "cool_dds_amp",
            FloatParam,
            "Cool light AOM DDS amplitude (0-1)",
            defaults.cool_amplitude,
            min=0.0,
            max=1.0,
        )
        self.cool_dds_amp: FloatParamHandle

        self.repump_dds_amp = self.setattr_param(
            "repump_dds_amp",
            FloatParam,
            "Repump light AOM DDS amplitude (0-1)",
            defaults.repump_amplitude,
            min=0.0,
            max=1.0,
        )
        self.repump_dds_amp: FloatParamHandle


class MOTLightSettings(_LightSettings):
    DEFAULTS = MOT_LIGHT_DEFAULTS


class MolassesLightSettings(_LightSettings):
    DEFAULTS = MOLASSES_LIGHT_DEFAULTS


class CoolingLightSettings(_LightSettings):
    DEFAULTS = COOLING_LIGHT_DEFAULTS


class ImagingLightSettings(_LightSettings):
    DEFAULTS = IMAGING_LIGHT_DEFAULTS


class _ShimSettings(Fragment):
    """Three low-field shim settings, deliberately excluding the quad demand."""

    DEFAULTS: ShimDefaults

    def build_fragment(self):
        defaults = self.DEFAULTS
        self.ew_setpoint = self.setattr_param(
            "ew_setpoint",
            FloatParam,
            "E/W shim servo setpoint voltage",
            defaults.ew,
            min=-10.0 * V,
            max=10.0 * V,
        )
        self.ew_setpoint: FloatParamHandle

        self.ud_setpoint = self.setattr_param(
            "ud_setpoint",
            FloatParam,
            "U/D shim servo setpoint voltage",
            defaults.ud,
            min=-10.0 * V,
            max=10.0 * V,
        )
        self.ud_setpoint: FloatParamHandle

        self.ns_setpoint = self.setattr_param(
            "ns_setpoint",
            FloatParam,
            "N/S shim servo setpoint voltage",
            defaults.ns,
            min=-10.0 * V,
            max=10.0 * V,
        )
        self.ns_setpoint: FloatParamHandle


class MOTShimSettings(_ShimSettings):
    DEFAULTS = MOT_SHIM_DEFAULTS


class MolassesShimSettings(_ShimSettings):
    DEFAULTS = MOLASSES_SHIM_DEFAULTS


class CoolingShimSettings(_ShimSettings):
    DEFAULTS = COOLING_SHIM_DEFAULTS


# -----------------------------------------------------------------------------
# Experimental stages
# -----------------------------------------------------------------------------


class RbMOTStage(UsesLabRTIOHardware):
    """Establish and hold one parameterised Rb MOT state."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("light", MOTLightSettings)
        self.light: MOTLightSettings
        self.setattr_fragment("shims", MOTShimSettings)
        self.shims: MOTShimSettings

        self.quad_setpoint = self.setattr_param(
            "quad_setpoint",
            FloatParam,
            "MOT quadrupole-coil demand voltage",
            MOT_QUAD_DEFAULT,
            min=0.0 * V,
            max=10.0 * V,
        )
        self.quad_setpoint: FloatParamHandle

        self.tweezer_setpoint = self.setattr_param(
            "tweezer_setpoint",
            FloatParam,
            "817 nm servo setpoint shared by MOT loading and molasses",
            TWEEZER_MOT_SETPOINT,
            min=0.0,
            max=10.0,
        )
        self.tweezer_setpoint: FloatParamHandle

        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "How long to hold the MOT before transfer",
            MOT_HOLD_TIME,
            min=1.0 * ms,
            max=10.0 * s,
        )
        self.duration: FloatParamHandle

    @kernel
    def run(self, turn_tweezer_servo_on, turn_light_on, shutter_prefire):
        """Establish and hold this complete MOT stage.

        Requires:
            If ``turn_tweezer_servo_on`` is false, the 817 nm servo is already
            enabled. If ``turn_light_on`` is false, the Rb shutters and RF switches
            are already on. ``shutter_prefire`` is used only when turning light on.

        During:
            Applies every MOT light, field, quadrupole, and tweezer setting, then waits
            for ``duration`` with the quadrupole TTL on.

        Leaves:
            The 817 nm servo, Rb cooling and repump light, MOT fields, and quadrupole
            TTL on at this stage's settings. Nothing is restored or turned off when
            this method returns.
        """
        # Program the target while output is disabled, avoiding a transient at the zero
        # offset installed by LabRTIOHardware.initialise().
        self.hardware.set_rb_tweezer_setpoint(self.tweezer_setpoint.use())
        if turn_tweezer_servo_on:
            self.hardware.set_rb_tweezer_servo_enabled(1)

        self.hardware.program_rb_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )
        self.hardware.set_fields_with_quad_demand(
            self.shims.ew_setpoint.use(),
            self.shims.ud_setpoint.use(),
            self.shims.ns_setpoint.use(),
            self.quad_setpoint.use(),
        )
        self.hardware.ttl_quad.on()

        if turn_light_on:
            self.hardware.turn_rb_light_on(shutter_prefire)

        delay(self.duration.use())
        # There is deliberately no exit action here: RTIO outputs latch, so the full
        # MOT state remains active for the following transition.


class RbMolassesStage(UsesLabRTIOHardware):
    """Transition from an active Rb MOT into a timed molasses state."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("light", MolassesLightSettings)
        self.light: MolassesLightSettings
        self.setattr_fragment("shims", MolassesShimSettings)
        self.shims: MolassesShimSettings

        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "How long to hold the molasses settings",
            MOLASSES_TIME,
            min=0.0 * ms,
            max=1.0 * s,
        )
        self.duration: FloatParamHandle

    @kernel
    def run(self, turn_light_off):
        """Enter from an active MOT, hold, and choose the exit light state.

        Requires:
            The Rb shutters and cooling/repump RF switches are already on, normally
            from :meth:`RbMOTStage.run`.

        During:
            Applies the molasses shim settings with zero analogue quadrupole demand,
            waits for the established settling intervals, programs the molasses light,
            disables the quadrupole TTL, and waits for ``duration``.

        Leaves:
            The molasses shim settings active, analogue quadrupole demand at zero, and
            quadrupole TTL off. The 817 nm servo is unchanged. Rb light is off only
            when ``turn_light_off`` is true; otherwise it remains on at the molasses
            settings. No previous hardware state is restored automatically.
        """
        self.hardware.set_fields_with_quad_demand_off(
            self.shims.ew_setpoint.use(),
            self.shims.ud_setpoint.use(),
            self.shims.ns_setpoint.use(),
        )
        delay(0.5 * ms)
        self.hardware.program_rb_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )
        delay(0.5 * ms)
        self.hardware.ttl_quad.off()
        delay(self.duration.use())

        if turn_light_off:
            self.hardware.turn_rb_light_off()


class RbCoolingStage(UsesLabRTIOHardware):
    """Apply a parameterised Rb tweezer-cooling interval."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("light", CoolingLightSettings)
        self.light: CoolingLightSettings
        self.setattr_fragment("shims", CoolingShimSettings)
        self.shims: CoolingShimSettings

        self.tweezer_setpoint = self.setattr_param(
            "tweezer_setpoint",
            FloatParam,
            "817 nm servo setpoint during cooling and imaging",
            TWEEZER_IMAGING_SETPOINT,
            min=0.0,
            max=10.0,
        )
        self.tweezer_setpoint: FloatParamHandle

        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "How long to cool before imaging",
            COOLING_TIME,
            min=0.0 * ms,
            max=1.0 * s,
        )
        self.duration: FloatParamHandle

    @kernel
    def run(self, turn_light_on, turn_light_off):
        """Establish and hold cooling with explicit resonant-light transitions.

        Requires:
            The 817 nm servo is already enabled and the quadrupole TTL is already off,
            normally from molasses. If ``turn_light_on`` is false, both Rb shutters
            and cooling/repump RF switches are already on.

        During:
            Applies every cooling light, shim, and tweezer setting, keeps the analogue
            quadrupole demand at zero, optionally opens the light path, and waits for
            ``duration``. Shutter prefire time is outside ``duration``.

        Leaves:
            The 817 nm servo enabled at ``tweezer_setpoint``, the cooling shim settings
            active, and analogue quadrupole demand at zero. The quadrupole TTL is
            unchanged and therefore remains off under the entry contract. Rb light is
            off only when ``turn_light_off`` is true; otherwise it remains on at the
            cooling settings. No previous hardware state is restored automatically.
        """
        self.hardware.set_rb_tweezer_setpoint(self.tweezer_setpoint.use())
        self.hardware.set_fields_with_quad_demand_off(
            self.shims.ew_setpoint.use(),
            self.shims.ud_setpoint.use(),
            self.shims.ns_setpoint.use(),
        )
        self.hardware.program_rb_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )

        if turn_light_on:
            self.hardware.turn_rb_light_on(SHUTTER_PREFIRE)

        delay(self.duration.use())

        if turn_light_off:
            self.hardware.turn_rb_light_off()


class RbImagingStage(UsesLabRTIOHardware):
    """Take one exposure using the parameterised Rb imaging light."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("light", ImagingLightSettings)
        self.light: ImagingLightSettings

        self.exposure_time = self.setattr_param(
            "exposure_time",
            FloatParam,
            "How long to expose the camera",
            CAMERA_EXPOSURE_TIME,
            min=1.0 * ms,
            max=1.0 * s,
        )
        self.exposure_time: FloatParamHandle

    @kernel
    def run(self, turn_light_off):
        """Program imaging light, expose, and choose the light exit state.

        Requires:
            The Rb shutters and cooling/repump RF switches are already on, and the
            camera is ready to accept an exposure trigger.

        During:
            Programs the imaging light, holds the camera-exposure TTL high for
            ``exposure_time``, then lowers the TTL.

        Leaves:
            The camera-exposure TTL off. Tweezer and field settings are unchanged. Rb
            light is off only when ``turn_light_off`` is true; otherwise it remains on
            at the imaging settings. No previous hardware state is restored
            automatically.
        """
        self.hardware.program_rb_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )
        self.hardware.ttl_camera_exposure.on()
        delay(self.exposure_time.use())
        self.hardware.ttl_camera_exposure.off()

        if turn_light_off:
            self.hardware.turn_rb_light_off()


# -----------------------------------------------------------------------------
# Reusable parts
# -----------------------------------------------------------------------------


class LoadRbMOTToTweezers(UsesLabRTIOHardware):
    """Load a Rb MOT and transition into molasses."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("mot", RbMOTStage, hardware=hardware)
        self.mot: RbMOTStage
        self.setattr_fragment("molasses", RbMolassesStage, hardware=hardware)
        self.molasses: RbMolassesStage

        self.shutter_prefire = self.setattr_param(
            "shutter_prefire",
            FloatParam,
            "Time between opening the Rb shutters and enabling RF",
            SHUTTER_PREFIRE,
            min=0.0 * ms,
            max=200.0 * ms,
        )
        self.shutter_prefire: FloatParamHandle

    @kernel
    def run(self, turn_light_off):
        """Run MOT then molasses, explicitly choosing the final resonant-light state.

        Requires:
            The shared hardware has been initialised. This part normally starts from
            the standard safe shot boundary.

        During:
            Programs and enables the 817 nm servo and Rb light, runs the complete MOT
            interval, then transitions into and holds the molasses interval.

        Leaves:
            The 817 nm servo enabled at the MOT tweezer setpoint, molasses shim and
            light settings programmed, and quadrupole demand and TTL off. Rb light is
            off only when ``turn_light_off`` is true; otherwise it remains on. No
            previous hardware state is restored automatically.
        """
        self.mot.run(
            turn_tweezer_servo_on=True,
            turn_light_on=True,
            shutter_prefire=self.shutter_prefire.use(),
        )
        self.molasses.run(turn_light_off=turn_light_off)


class CoolAndImageRbAtoms(UsesLabRTIOHardware):
    """Cool and image Rb with explicit resonant-light entry and exit choices."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("cooling", RbCoolingStage, hardware=hardware)
        self.cooling: RbCoolingStage
        self.setattr_fragment("imaging", RbImagingStage, hardware=hardware)
        self.imaging: RbImagingStage

    @kernel
    def run(self, turn_light_on, turn_light_off):
        """Run cooling and imaging with explicit light transitions.

        Requires:
            The 817 nm servo is already enabled, the quadrupole TTL is off, and the
            camera is ready for an exposure. If ``turn_light_on`` is false, the Rb
            light path is already on, normally from molasses.

        During:
            Runs the complete cooling interval, changes to the imaging light settings,
            and takes one exposure.

        Leaves:
            The 817 nm servo enabled at the cooling/imaging setpoint, cooling shim
            settings active, analogue quadrupole demand at zero, quadrupole TTL off,
            and camera-exposure TTL off. Rb light is off only when ``turn_light_off``
            is true; otherwise it remains on at the imaging settings. No previous
            hardware state is restored automatically.
        """
        self.cooling.run(
            turn_light_on=turn_light_on,
            turn_light_off=False,
        )
        self.imaging.run(turn_light_off=turn_light_off)
