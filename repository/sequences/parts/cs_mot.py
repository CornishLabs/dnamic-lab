"""Reusable Cs MOT-to-tweezers settings, stages, and operations.

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
from artiq.language.units import MHz, V, us, ms, s

from ndscan.define.fragment import Fragment
from ndscan.define.parameters import FloatParam, FloatParamHandle

from .imaging import CAMERA_EXPOSURE_TIME
from .lab_hardware import UsesLabRTIOHardware

# -----------------------------------------------------------------------------
# Experiment defaults
# -----------------------------------------------------------------------------

TWEEZER_MOT_SETPOINT = 6.5
TWEEZER_TWO_STAGE_MOT_SETPOINT = 6.5
TWEEZER_IMAGING_SETPOINT = 5.7
TWEEZER_SPILL_SETPOINT = 3.5

SHUTTER_PREFIRE = 10.0 * ms
MOT_HOLD_TIME = 0.2 * s
BULK_MOT_HOLD_TIME = 120.0 * ms
COMPRESSED_MOT_HOLD_TIME = 100.0 * ms
MOLASSES_TIME = 35.0 * ms
COOLING_TIME = 10.0 * ms
SPILL_TIME = 20.0 * ms
DROP_TIME = 100.0 * us


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
    cool_frequency=99.45 * MHz,
    repump_frequency=94.17 * MHz,
    cool_amplitude=0.58,
    repump_amplitude=0.36,
)
MOT_SHIM_DEFAULTS = ShimDefaults(ew=0.04 * V, ud=0.51 * V, ns=-0.25 * V)
MOT_QUAD_DEFAULT = 8.8 * V

# The two-stage loading recipe has its own defaults.  Keeping these separate from
# ``MOT_*_DEFAULTS`` means adopting an optimised bulk/compressed sequence does not
# silently alter experiments which still use the ordinary one-stage Cs MOT.
BULK_MOT_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=99.5 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.58,
    repump_amplitude=MOT_LIGHT_DEFAULTS.repump_amplitude,
)
BULK_MOT_SHIM_DEFAULTS = ShimDefaults(
    ew=0.03 * V,
    ud=0.52 * V,
    ns=-0.25 * V,
)
BULK_MOT_QUAD_DEFAULT = 8.80 * V

COMPRESSED_MOT_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=99.5 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.60,
    repump_amplitude=MOT_LIGHT_DEFAULTS.repump_amplitude,
)
COMPRESSED_MOT_SHIM_DEFAULTS = ShimDefaults(
    ew=0.00 * V,
    ud=0.56 * V,
    ns=-0.27 * V,
)
COMPRESSED_MOT_QUAD_DEFAULT = 8.50 * V

MOLASSES_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=118.0 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.62,
    repump_amplitude=0.37,
)
MOLASSES_SHIM_DEFAULTS = ShimDefaults(ew=-0.13 * V, ud=1.05 * V, ns=0.07 * V)

COOLING_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=121.8 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.62,
    repump_amplitude=0.2,
)
COOLING_SHIM_DEFAULTS = ShimDefaults(ew=-0.13 * V, ud=1.05 * V, ns=0.07 * V)

IMAGING_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=108.0 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.58,
    repump_amplitude=0.2,
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
            unit="MHz",
        )
        self.cool_frequency: FloatParamHandle

        self.repump_frequency = self.setattr_param(
            "repump_frequency",
            FloatParam,
            "Repump light AOM drive frequency",
            defaults.repump_frequency,
            min=30.0 * MHz,
            max=130.0 * MHz,
            unit="MHz",
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


class BulkMOTLightSettings(MOTLightSettings):
    """Cooling/repump controls for the first stage of a two-stage MOT."""

    DEFAULTS = BULK_MOT_LIGHT_DEFAULTS


class CompressedMOTLightSettings(MOTLightSettings):
    """Cooling/repump controls for the second stage of a two-stage MOT."""

    DEFAULTS = COMPRESSED_MOT_LIGHT_DEFAULTS


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


class BulkMOTShimSettings(MOTShimSettings):
    """Low-field controls for the first stage of a two-stage MOT."""

    DEFAULTS = BULK_MOT_SHIM_DEFAULTS


class CompressedMOTShimSettings(MOTShimSettings):
    """Low-field controls for the second stage of a two-stage MOT."""

    DEFAULTS = COMPRESSED_MOT_SHIM_DEFAULTS


class MolassesShimSettings(_ShimSettings):
    DEFAULTS = MOLASSES_SHIM_DEFAULTS


class CoolingShimSettings(_ShimSettings):
    DEFAULTS = COOLING_SHIM_DEFAULTS


# -----------------------------------------------------------------------------
# Experimental stages
# -----------------------------------------------------------------------------


class CsMOTStage(UsesLabRTIOHardware):
    """Establish and hold one parameterised Cs MOT state."""

    # Subclasses can specialise a complete stage without changing the ordinary
    # one-stage MOT defaults used elsewhere.
    DURATION_DEFAULT = MOT_HOLD_TIME
    LIGHT_SETTINGS_CLASS = MOTLightSettings
    SHIM_SETTINGS_CLASS = MOTShimSettings
    QUAD_DEFAULT = MOT_QUAD_DEFAULT
    TWEEZER_DEFAULT = TWEEZER_MOT_SETPOINT

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("light", self.LIGHT_SETTINGS_CLASS)
        self.light: MOTLightSettings
        self.setattr_fragment("shims", self.SHIM_SETTINGS_CLASS)
        self.shims: MOTShimSettings

        self.quad_setpoint = self.setattr_param(
            "quad_setpoint",
            FloatParam,
            "MOT quadrupole-coil demand voltage",
            self.QUAD_DEFAULT,
            min=0.0 * V,
            max=10.0 * V,
            unit="V"
        )
        self.quad_setpoint: FloatParamHandle

        self.tweezer_setpoint = self.setattr_param(
            "tweezer_setpoint",
            FloatParam,
            "1066 nm servo setpoint shared by MOT loading and molasses",
            self.TWEEZER_DEFAULT,
            min=0.0,
            max=10.0,
            unit="V",
        )
        self.tweezer_setpoint: FloatParamHandle

        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "How long to hold the MOT loading",
            self.DURATION_DEFAULT,
            min=1.0 * ms,
            max=10.0 * s,
            unit="ms"
        )
        self.duration: FloatParamHandle

    @kernel
    def _apply_settings(self):
        """Apply this stage while the Cs light path and tweezer servo stay active.

        This private helper contains only the common parameter application. ``run()``
        owns the entry choices and timed hold.
        """
        self.hardware.set_cs_tweezer_setpoint(self.tweezer_setpoint.use())
        self.hardware.program_cs_light(
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

    @kernel
    def run(self, turn_tweezer_servo_on, turn_light_on, shutter_prefire):
        """Establish and hold this complete MOT stage.

        Requires:
            If ``turn_tweezer_servo_on`` is false, the 1066 nm servo is already
            enabled. If ``turn_light_on`` is false, the Cs shutters and RF switches
            are already on. ``shutter_prefire`` is used only when turning light on.

        During:
            Applies every MOT light, field, quadrupole, and tweezer setting, then waits
            for ``duration`` with the quadrupole TTL on.

        Leaves:
            The 1066 nm servo, Cs cooling and repump light, MOT fields, and quadrupole
            TTL on at this stage's settings. Nothing is restored or turned off when
            this method returns.
        """
        if turn_tweezer_servo_on:
            # Preserve the established Cs ordering: enable the servo before changing
            # its target.
            self.hardware.set_cs_tweezer_servo_enabled(1)

        self._apply_settings()

        if turn_light_on:
            self.hardware.turn_cs_light_on(shutter_prefire)

        delay(self.duration.use())
        # There is deliberately no exit action here: RTIO outputs latch, so the full
        # MOT state remains active for the following transition.


class CsBulkMOTStage(CsMOTStage):
    """Bulk-loading stage of the two-stage Cs MOT."""

    DURATION_DEFAULT = BULK_MOT_HOLD_TIME
    LIGHT_SETTINGS_CLASS = BulkMOTLightSettings
    SHIM_SETTINGS_CLASS = BulkMOTShimSettings
    QUAD_DEFAULT = BULK_MOT_QUAD_DEFAULT
    TWEEZER_DEFAULT = TWEEZER_TWO_STAGE_MOT_SETPOINT


class CsCompressedMOTStage(CsMOTStage):
    """Compressed-MOT stage following bulk loading."""

    DURATION_DEFAULT = COMPRESSED_MOT_HOLD_TIME
    LIGHT_SETTINGS_CLASS = CompressedMOTLightSettings
    SHIM_SETTINGS_CLASS = CompressedMOTShimSettings
    QUAD_DEFAULT = COMPRESSED_MOT_QUAD_DEFAULT
    TWEEZER_DEFAULT = TWEEZER_TWO_STAGE_MOT_SETPOINT


class CsMolassesStage(UsesLabRTIOHardware):
    """Transition from an active Cs MOT into a timed molasses state."""

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
            The Cs shutters and cooling/repump RF switches are already on, normally
            from :meth:`CsMOTStage.run`.

        During:
            Programs the molasses light and shim settings, sets the analogue
            quadrupole demand to zero, disables the quadrupole TTL, and waits for
            ``duration``.

        Leaves:
            The molasses shim settings active, analogue quadrupole demand at zero, and
            quadrupole TTL off. The 1066 nm servo is unchanged. Cs light is off only
            when ``turn_light_off`` is true; otherwise it remains on at the molasses
            settings. No previous hardware state is restored automatically.
        """
        self.hardware.program_cs_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )
        self.hardware.ttl_quad.off()
        self.hardware.set_fields_with_quad_demand_off(
            self.shims.ew_setpoint.use(),
            self.shims.ud_setpoint.use(),
            self.shims.ns_setpoint.use(),
        )
        delay(self.duration.use())

        if turn_light_off:
            self.hardware.turn_cs_light_off()


class CsCoolingStage(UsesLabRTIOHardware):
    """Apply a parameterised Cs tweezer-cooling interval.

    The caller explicitly chooses whether this operation turns the resonant light on
    at entry and off at exit. These are composition choices rather than scannable
    parameters, so every call site makes its expected light transition visible.
    """

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("light", CoolingLightSettings)
        self.light: CoolingLightSettings
        self.setattr_fragment("shims", CoolingShimSettings)
        self.shims: CoolingShimSettings

        self.tweezer_setpoint = self.setattr_param(
            "tweezer_setpoint",
            FloatParam,
            "1066 nm servo setpoint during cooling and imaging",
            TWEEZER_IMAGING_SETPOINT,
            min=0.0,
            max=10.0,
        )
        self.tweezer_setpoint: FloatParamHandle

        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "How long to hold the Cs tweezer-cooling settings",
            COOLING_TIME,
            min=0.0 * ms,
            max=1.0 * s,
        )
        self.duration: FloatParamHandle

    @kernel
    def run(self, turn_light_on, turn_light_off):
        """Establish and hold this cooling profile.

        Requires:
            The 1066 nm servo is already enabled. If ``turn_light_on`` is false, both
            Cs shutters and cooling/repump RF switches are already on, normally from
            molasses.

        During:
            Applies every cooling light, shim, and tweezer setting, ensures the
            quadrupole demand and TTL are off, optionally opens the light path, and
            waits for ``duration``. Shutter prefire time is outside ``duration``.

        Leaves:
            The 1066 nm servo enabled at ``tweezer_setpoint``, the cooling shim
            settings active, and the quadrupole demand and TTL off. Cs light is off
            only when ``turn_light_off`` is true; otherwise it remains on at the
            cooling settings. No previous hardware state is restored automatically.
        """
        self.hardware.set_cs_tweezer_setpoint(self.tweezer_setpoint.use())
        self.hardware.set_fields_with_quad_demand_off(
            self.shims.ew_setpoint.use(),
            self.shims.ud_setpoint.use(),
            self.shims.ns_setpoint.use(),
        )
        self.hardware.program_cs_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )

        # Cooling never uses the quadrupole field. The analogue demand was set to zero
        # above before disabling its TTL, preserving the established hardware order.
        self.hardware.ttl_quad.off()

        if turn_light_on:
            self.hardware.turn_cs_light_on(SHUTTER_PREFIRE)

        # The shutter prefire is outside this delay: duration measures only the time
        # for which atoms see the programmed cooling and repump RF.
        delay(self.duration.use())

        if turn_light_off:
            self.hardware.turn_cs_light_off()


class CsImagingStage(UsesLabRTIOHardware):
    """Take one exposure using the parameterised Cs imaging light."""

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
            The Cs shutters and cooling/repump RF switches are already on, and the
            camera is ready to accept an exposure trigger.

        During:
            Programs the imaging light, holds the camera-exposure TTL high for
            ``exposure_time``, then lowers the TTL.

        Leaves:
            The camera-exposure TTL off. Tweezer and field settings are unchanged. Cs
            light is off only when ``turn_light_off`` is true; otherwise it remains on
            at the imaging settings. No previous hardware state is restored
            automatically.
        """
        self.hardware.program_cs_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )
        self.hardware.ttl_camera_exposure.on()
        delay(self.exposure_time.use())
        self.hardware.ttl_camera_exposure.off()

        if turn_light_off:
            self.hardware.turn_cs_light_off()


# -----------------------------------------------------------------------------
# Reusable parts
# -----------------------------------------------------------------------------


class LoadCsMOTToTweezers(UsesLabRTIOHardware):
    """Load a Cs MOT and transition into molasses."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("mot", CsMOTStage, hardware=hardware)
        self.mot: CsMOTStage
        self.setattr_fragment("molasses", CsMolassesStage, hardware=hardware)
        self.molasses: CsMolassesStage

        # Shutter prefire describes entry into the complete loading operation. It is
        # not a property of every MOT stage (in particular, a compressed stage entered
        # from an active MOT must not expose an unused copy).
        self.shutter_prefire = self.setattr_param(
            "shutter_prefire",
            FloatParam,
            "Time between opening the Cs shutters and enabling RF",
            SHUTTER_PREFIRE,
            min=0.0 * ms,
            max=200.0 * ms,
            unit="ms",
        )
        self.shutter_prefire: FloatParamHandle

    @kernel
    def run(self, turn_light_off):
        """Run MOT then molasses, explicitly choosing the final resonant-light state.

        Requires:
            The shared hardware has been initialised. This part normally starts from
            the standard safe shot boundary.

        During:
            Enables the 1066 nm servo and Cs light, runs the complete MOT interval,
            then transitions into and holds the molasses interval.

        Leaves:
            The 1066 nm servo enabled at the MOT tweezer setpoint, molasses shim and
            light settings programmed, and quadrupole demand and TTL off. Cs light is
            off only when ``turn_light_off`` is true; otherwise it remains on. No
            previous hardware state is restored automatically.
        """
        self.mot.run(
            turn_tweezer_servo_on=True,
            turn_light_on=True,
            shutter_prefire=self.shutter_prefire.use(),
        )
        self.molasses.run(turn_light_off=turn_light_off)


class LoadTwoStageCsMOTToTweezers(UsesLabRTIOHardware):
    """Load Cs through bulk-MOT, compressed-MOT and molasses stages.

    The two stages have independent defaults selected from the Cs loading
    optimisation, while retaining one shared tweezer setpoint.  They remain
    independently scannable so later experiments can refine either stage.
    """

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("bulk_mot", CsBulkMOTStage, hardware=hardware)
        self.bulk_mot: CsBulkMOTStage
        self.setattr_fragment(
            "compressed_mot",
            CsCompressedMOTStage,
            hardware=hardware,
        )
        self.compressed_mot: CsCompressedMOTStage

        # The stages are independently parameterised except for trap depth.  A
        # single parent-owned parameter is rebound into both children, so dashboard
        # changes and scans cannot accidentally give them different 1066-nm
        # setpoints.
        self.tweezer_setpoint = self.setattr_param_rebind(
            "tweezer_setpoint",
            self.bulk_mot,
            description="1066 nm servo setpoint shared by both Cs MOT stages",
        )
        self.tweezer_setpoint: FloatParamHandle
        self.compressed_mot.bind_param(
            "tweezer_setpoint",
            self.tweezer_setpoint,
        )

        self.setattr_fragment("molasses", CsMolassesStage, hardware=hardware)
        self.molasses: CsMolassesStage

        self.shutter_prefire = self.setattr_param(
            "shutter_prefire",
            FloatParam,
            "Time between opening the Cs shutters and enabling RF",
            SHUTTER_PREFIRE,
            min=0.0 * ms,
            max=200.0 * ms,
            unit="ms",
        )
        self.shutter_prefire: FloatParamHandle

    @kernel
    def run(self, turn_light_off):
        """Run both MOT stages and molasses with an explicit light exit.

        Requires:
            The shared hardware has been initialised. This part normally starts from
            the standard safe shot boundary.

        During:
            Enables the 1066 nm servo and Cs light, runs bulk MOT, changes every
            parameterised MOT setting without interrupting the light or servo, runs
            compressed MOT, then transitions into and holds molasses.

        Leaves:
            The 1066 nm servo enabled at the shared MOT tweezer setpoint, molasses shim
            and light settings programmed, and quadrupole demand and TTL off. Cs light
            is off only when ``turn_light_off`` is true; otherwise it remains on. No
            previous hardware state is restored automatically.
        """
        self.bulk_mot.run(
            turn_tweezer_servo_on=True,
            turn_light_on=True,
            shutter_prefire=self.shutter_prefire.use(),
        )

        # Both resources remain active across this boundary. Reapplying every
        # compressed-stage setting keeps the stage independently scannable.
        self.compressed_mot.run(
            turn_tweezer_servo_on=False,
            turn_light_on=False,
            shutter_prefire=0.0 * s,
        )

        self.molasses.run(turn_light_off=turn_light_off)


class CoolAndImageCsAtoms(UsesLabRTIOHardware):
    """Cool and image Cs with explicit resonant-light entry and exit choices."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("cooling", CsCoolingStage, hardware=hardware)
        self.cooling: CsCoolingStage
        self.setattr_fragment("imaging", CsImagingStage, hardware=hardware)
        self.imaging: CsImagingStage

    @kernel
    def run(self, turn_light_on, turn_light_off):
        """Run cooling and imaging with explicit light transitions.

        Requires:
            The 1066 nm servo is already enabled and the camera is ready for an
            exposure. If ``turn_light_on`` is false, the Cs light path is already on,
            normally from molasses.

        During:
            Runs the complete cooling interval, changes to the imaging light settings,
            and takes one exposure.

        Leaves:
            The 1066 nm servo enabled at the cooling/imaging setpoint, cooling shim
            settings active, quadrupole demand and TTL off, and camera-exposure TTL
            off. Cs light is off only when ``turn_light_off`` is true; otherwise it
            remains on at the imaging settings. No previous hardware state is restored
            automatically.
        """
        self.cooling.run(
            turn_light_on=turn_light_on,
            turn_light_off=False,
        )
        self.imaging.run(turn_light_off=turn_light_off)


class SpillHotCsAtoms(UsesLabRTIOHardware):
    """Lower the closed-loop 1066 nm trap briefly so energetic atoms escape."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)

        self.setpoint = self.setattr_param(
            "setpoint",
            FloatParam,
            "1066 nm servo setpoint during the hot-atom spill",
            TWEEZER_SPILL_SETPOINT,
            min=0.0,
            max=10.0,
        )
        self.setpoint: FloatParamHandle

        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "How long to hold the 1066 nm trap at the spill setpoint",
            SPILL_TIME,
            min=0.0 * ms,
            max=1.0 * s,
            unit="us",
        )
        self.duration: FloatParamHandle

    @kernel
    def run(self):
        """Apply and hold the spill setpoint.

        Requires:
            The Cs tweezer servo is already enabled.

        During:
            Changes the closed-loop target to ``setpoint`` and waits for ``duration``.

        Leaves:
            The servo enabled at the spill setpoint. It is deliberately not restored;
            the following stage must explicitly establish the target it needs.
        """
        # set_cs_tweezer_setpoint() changes the target of the still-closed loop; it
        # does not disable the servo or directly impose a DDS amplitude.
        self.hardware.set_cs_tweezer_setpoint(self.setpoint.use())
        delay(self.duration.use())


class FastTrapDrop(UsesLabRTIOHardware):
    """Drop the 1066 nm trap briefly using its output RF switch."""

    def build_fragment(self, hardware):
        self._use_hardware(hardware)

        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "How long to drop the 1066 nm trap",
            DROP_TIME,
            min=0.0 * ms,
            max=1.0 * s,
            unit="us",
        )
        self.duration: FloatParamHandle

    @kernel
    def run(self):
        """Perform a fast trap extinction and restore the held output.

        Requires:
            The Cs tweezer output is on.

        During:
            Holds IIR updates, switches the RF output off for ``duration``, then
            switches the output back on.

        Leaves:
            The RF output on with IIR updates still held, exactly as implemented by
            ``hardware.drop_cs_trap()``. The caller must explicitly re-enable
            closed-loop updates if it requires them.
        """
        self.hardware.drop_cs_trap(self.duration.use())
