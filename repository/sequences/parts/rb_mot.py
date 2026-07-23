"""Reusable Rb MOT-to-tweezers settings, stages, and operations.

This module deliberately contains no complete experiment or result analysis.  A shot
passes the globally owned ``LabRTIOHardware`` instance to the parts here as a non-owning
reference.  Stage settings remain separate and independently scannable even though the
stages act on the same physical devices.
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
    """Establish the MOT state from the safe per-point hardware state."""

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

        self.shutter_prefire = self.setattr_param(
            "shutter_prefire",
            FloatParam,
            "Time between opening the Rb shutters and enabling RF",
            SHUTTER_PREFIRE,
            min=0.0 * ms,
            max=200.0 * ms,
        )
        self.shutter_prefire: FloatParamHandle

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
    def establish(self):
        """Establish the same MOT state, in the same order, as ``rb_mot.py``."""
        # Program the target while output is disabled, avoiding a transient at the zero
        # offset installed by LabRTIOHardware.initialise().
        self.hardware.set_rb_tweezer_setpoint(self.tweezer_setpoint.use())
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
        self.hardware.turn_quad_on()
        self.hardware.turn_rb_light_on(self.shutter_prefire.use())


class RbMolassesStage(UsesLabRTIOHardware):
    """Differential MOT -> molasses transition.

    Requires the light shutters and RF switches to remain on from
    :meth:`RbMOTStage.establish`.  It guarantees a zero quad demand and then disables
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
        self.hardware.program_rb_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )
        delay(0.5 * ms)
        self.hardware.turn_quad_off()


class RbCoolingStage(UsesLabRTIOHardware):
    """Differential molasses -> tweezer-cooling transition."""

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
    def enter_from_molasses(self):
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

    @kernel
    def enter_from_dark_hold(self):
        """Establish cooling when the Rb resonant light was deliberately shut off."""
        self.enter_from_molasses()
        self.hardware.turn_rb_light_on(SHUTTER_PREFIRE)


class RbImagingStage(UsesLabRTIOHardware):
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
        self.hardware.program_rb_light(
            self.light.cool_frequency.use(),
            self.light.repump_frequency.use(),
            self.light.cool_dds_amp.use(),
            self.light.repump_dds_amp.use(),
        )
        self.hardware.start_camera_exposure()
        delay(self.exposure_time.use())
        self.hardware.stop_camera_exposure()
        self.hardware.turn_rb_light_off()


# -----------------------------------------------------------------------------
# Reusable parts
# -----------------------------------------------------------------------------


class LoadRbMOTToTweezers(UsesLabRTIOHardware):
    """Load a Rb MOT and perform the differential transition to molasses.

    Ensures that the tweezer servo and Rb light remain on, the quad is off, and the
    molasses field/light settings are active.  This is the state expected by
    :class:`CoolAndImageRbAtoms` below.
    """

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("mot", RbMOTStage, hardware=hardware)
        self.mot: RbMOTStage
        self.setattr_fragment("molasses", RbMolassesStage, hardware=hardware)
        self.molasses: RbMolassesStage

    @kernel
    def run(self):
        self.mot.establish()
        delay(self.mot.duration.use())
        self.molasses.enter_from_mot()
        delay(self.molasses.duration.use())

    @kernel
    def run_to_dark_hold(self):
        """Load Rb, then turn off resonant light while leaving 817 nm on."""
        self.run()
        self.hardware.turn_rb_light_off()


class CoolAndImageRbAtoms(UsesLabRTIOHardware):
    """Cool and image Rb atoms starting from ``LoadRbMOTToTweezers.run()``.

    This intentionally exposes ``run_from_molasses`` rather than a misleading generic
    ``run``: the existing fast transition assumes that light is already on and that the
    shutters remain open from MOT loading.
    """

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.setattr_fragment("cooling", RbCoolingStage, hardware=hardware)
        self.cooling: RbCoolingStage
        self.setattr_fragment("imaging", RbImagingStage, hardware=hardware)
        self.imaging: RbImagingStage

    @kernel
    def run_from_molasses(self):
        self.cooling.enter_from_molasses()
        delay(self.cooling.duration.use())
        self.imaging.enter_from_cooling()

    @kernel
    def run_from_dark_hold(self):
        """Restart Rb cooling light, cool, and image atoms held in 817 nm."""
        self.cooling.enter_from_dark_hold()
        delay(self.cooling.duration.use())
        self.imaging.enter_from_cooling()
