"""Reusable Cs MOT-to-tweezers settings, stages, and operations.

This module deliberately contains no complete experiment or result analysis.  A shot
passes the globally owned ``LabRTIOHardware`` instance to the parts here as a non-owning
reference.  Stage settings remain separate and independently scannable even though the
stages act on the same physical devices.
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

TWEEZER_MOT_SETPOINT = 6.567
TWEEZER_TWO_STAGE_MOT_SETPOINT = 6.50
TWEEZER_IMAGING_SETPOINT = 5.65
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
    cool_frequency=114.2 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.58,
    repump_amplitude=0.37,
)
MOLASSES_SHIM_DEFAULTS = ShimDefaults(ew=-0.15 * V, ud=1.05 * V, ns=0.5 * V)

COOLING_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=119.0 * MHz,
    repump_frequency=MOT_LIGHT_DEFAULTS.repump_frequency,
    cool_amplitude=0.6,
    repump_amplitude=0.2,
)
COOLING_SHIM_DEFAULTS = ShimDefaults(ew=-0.15 * V, ud=1.15 * V, ns=0.5 * V)

IMAGING_LIGHT_DEFAULTS = LightDefaults(
    cool_frequency=105.0 * MHz,
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
    """Establish the MOT state from the safe per-point hardware state."""

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
        )
        self.quad_setpoint: FloatParamHandle

        self.tweezer_setpoint = self.setattr_param(
            "tweezer_setpoint",
            FloatParam,
            "1066 nm servo setpoint shared by MOT loading and molasses",
            self.TWEEZER_DEFAULT,
            min=0.0,
            max=10.0,
        )
        self.tweezer_setpoint: FloatParamHandle

        self.shutter_prefire = self.setattr_param(
            "shutter_prefire",
            FloatParam,
            "Time between opening the Cs shutters and enabling RF",
            SHUTTER_PREFIRE,
            min=0.0 * ms,
            max=200.0 * ms,
        )
        self.shutter_prefire: FloatParamHandle

        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "How long to hold the MOT before transfer",
            self.DURATION_DEFAULT,
            min=1.0 * ms,
            max=10.0 * s,
        )
        self.duration: FloatParamHandle

    @kernel
    def apply_settings(self):
        """Apply this stage while the Cs light path and tweezer servo stay active.

        This is the useful transition between adjacent MOT stages: it changes every
        parameter which defines the stage without closing and reopening shutters or
        restarting the tweezer servo.  The caller is responsible for the timed hold.
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
        self.hardware.turn_quad_on()

    @kernel
    def establish(self):
        """Establish the same MOT state, in the same order, as ``cs_mot.py``."""
        # Preserve the existing order: enable the servo before changing its target.
        self.hardware.set_cs_tweezer_servo_enabled(1)
        self.apply_settings()
        self.hardware.turn_cs_light_on(self.shutter_prefire.use())


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
    """Differential MOT -> molasses transition.

    Requires the light shutters and RF switches to remain on from
    :meth:`CsMOTStage.establish`.  It guarantees a zero quad demand and then disables
    the quad TTL after reproducing the two original 0.5 ms settling delays.
    """

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
    def enter_from_mot(self):
        self.hardware.set_fields_with_quad_demand_off(
            self.shims.ew_setpoint.use(),
            self.shims.ud_setpoint.use(),
            self.shims.ns_setpoint.use(),
        )
        delay(0.5 * ms)
        self.hardware.program_cs_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )
        delay(0.5 * ms)
        self.hardware.turn_quad_off()


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
        """Establish this cooling profile, hold it for ``duration``, then return.

        With ``turn_light_on=True``, both Cs shutters are opened, the shutter prefire
        elapses, and cooling and repump RF are enabled before the timed interval.
        Otherwise the shutters and RF must already be on, as they are immediately
        after the molasses stage.

        With ``turn_light_off=True``, both RF paths are disabled and both shutters are
        closed afterwards. Otherwise the light remains on for the next operation.
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
        self.hardware.turn_quad_off()

        if turn_light_on:
            self.hardware.turn_cs_light_on(SHUTTER_PREFIRE)

        # The shutter prefire is outside this delay: duration measures only the time
        # for which atoms see the programmed cooling and repump RF.
        delay(self.duration.use())

        if turn_light_off:
            self.hardware.turn_cs_light_off()


class CsImagingStage(UsesLabRTIOHardware):
    """Image atoms, assuming cooling light is already on and shutters are open."""

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
    def enter_from_cooling(self):
        self.hardware.program_cs_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )
        self.hardware.start_camera_exposure()
        delay(self.exposure_time.use())
        self.hardware.stop_camera_exposure()
        self.hardware.turn_cs_light_off()


# -----------------------------------------------------------------------------
# Reusable parts
# -----------------------------------------------------------------------------


class LoadCsMOTToTweezers(UsesLabRTIOHardware):
    """Load a Cs MOT and perform the differential transition to molasses.

    Ensures that the tweezer servo and Cs light remain on, the quad is off, and the
    molasses field/light settings are active.  This is the state expected by
    :class:`CoolAndImageCsAtoms` below.
    """

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("mot", CsMOTStage, hardware=hardware)
        self.mot: CsMOTStage
        self.setattr_fragment("molasses", CsMolassesStage, hardware=hardware)
        self.molasses: CsMolassesStage

    @kernel
    def run(self):
        """Holds mot stage for duration, then switches to holding molasses, leaving cursor after
        molasses time is up. BUT does not turn the cool/repump light off after."""
        self.mot.establish()
        delay(self.mot.duration.use())
        self.molasses.enter_from_mot()
        delay(self.molasses.duration.use())

    @kernel
    def run_to_dark_hold(self):
        """Load Cs, then turn off resonant light while leaving 1066 nm on."""
        self.run()
        self.hardware.turn_cs_light_off()


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

    @kernel
    def run(self):
        """Run all three stages, leaving the Cs resonant light and servo on."""
        self.bulk_mot.establish()
        delay(self.bulk_mot.duration.use())

        # Light and tweezer output remain on across this boundary.  Reprogramming all
        # stage settings here makes the second stage self-contained and scannable.
        self.compressed_mot.apply_settings()
        delay(self.compressed_mot.duration.use())

        self.molasses.enter_from_mot()
        delay(self.molasses.duration.use())

    @kernel
    def run_to_dark_hold(self):
        """Run both MOT stages and molasses, then turn off the resonant light."""
        self.run()
        self.hardware.turn_cs_light_off()


class CoolAndImageCsAtoms(UsesLabRTIOHardware):
    """Cool and image Cs atoms starting from ``LoadCsMOTToTweezers.run()``.

    This intentionally exposes ``run_from_molasses`` rather than a misleading generic
    ``run``: the existing fast transition assumes that light is already on and that the
    shutters remain open from MOT loading.
    """

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("cooling", CsCoolingStage, hardware=hardware)
        self.cooling: CsCoolingStage
        self.setattr_fragment("imaging", CsImagingStage, hardware=hardware)
        self.imaging: CsImagingStage

    @kernel
    def run_from_molasses(self):
        self.cooling.run(
            turn_light_on=False,
            turn_light_off=False,
        )
        self.imaging.enter_from_cooling()

    @kernel
    def run_from_dark_hold(self):
        """Restart Cs cooling light, cool, and image atoms held in 1066 nm."""
        self.cooling.run(
            turn_light_on=True,
            turn_light_off=False,
        )
        self.imaging.enter_from_cooling()


class SpillHotCsAtoms(UsesLabRTIOHardware):
    """Lower the closed-loop 1066 nm trap briefly so energetic atoms escape.

    Requires the Cs tweezer servo to be enabled.  ``run()`` deliberately leaves the
    servo at the spill setpoint: the following stage must explicitly establish the
    state it needs.  In the usual load/spill/image sequence,
    :meth:`CoolAndImageCsAtoms.run_from_dark_hold` restores the cooling/imaging
    setpoint before it turns the resonant Cs light back on.
    """

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
        # set_cs_tweezer_setpoint() changes the target of the still-closed loop; it
        # does not disable the servo or directly impose a DDS amplitude.
        self.hardware.set_cs_tweezer_setpoint(self.setpoint.use())
        delay(self.duration.use())


class FastTrapDrop(UsesLabRTIOHardware):
    """Drop the 1066 nm trap briefly with the output RF switch so energetic atoms escape.
    It also holds the integrator state and resumes it after so there should be no servo problems.
    """

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
        # set_cs_tweezer_setpoint() changes the target of the still-closed loop; it
        # does not disable the servo or directly impose a DDS amplitude.
        self.hardware.drop_cs_trap(self.duration.use())
