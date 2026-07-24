"""Atom experiment: two-image Cs release-and-recapture measurement."""

from artiq.coredevice.core import Core
from artiq.experiment import kernel
from artiq.language.core import delay
from artiq.language.units import ms, us

from ndscan.define.fragment import ExpFragment
from ndscan.runtime.api import make_fragment_prepared_dashboard_scan_exp

from repository.sequences.parts.cs_mot import (
    CoolAndImageCsAtoms,
    CsCoolingStage,
    FastTrapDrop,
    LoadCsMOTToTweezers,
)
from repository.sequences.parts.imaging import (
    CS_TWEEZER_ROIS,
    CS_TWEEZER_THRESHOLDS,
    AtomImageReadout,
    ConditionalProbability,
    ImageSlot,
)
from repository.sequences.parts.lab_hardware import LabEnvironment
from repository.sequences.parts.repetition import (
    make_repeated_image_shot_statistics,
)

SHOTS_PER_POINT = 50

# The condition language evaluates one expression independently in each logical ROI
# group.  Here, every tweezer site is an independent copy of the same experiment, so
# represent the established nine Cs rectangles as nine groups containing one ROI each.
# This changes only the analysis shape; the physical rectangles and thresholds remain
# exactly the shared Cs defaults.
PARAMETRIC_HEATING_ROIS = tuple((roi,) for roi in CS_TWEEZER_ROIS[0])
PARAMETRIC_HEATING_THRESHOLDS = tuple(
    (threshold,) for threshold in CS_TWEEZER_THRESHOLDS[0]
)

# Declare the two expected images and the result name attached to each one.
INITIAL_TWEEZER_IMAGE = ImageSlot(
    "initial_tweezer",
    PARAMETRIC_HEATING_ROIS,
    PARAMETRIC_HEATING_THRESHOLDS,
)

FINAL_TWEEZER_IMAGE = ImageSlot(
    "final_tweezer",
    PARAMETRIC_HEATING_ROIS,
    PARAMETRIC_HEATING_THRESHOLDS,
)

# For each tweezer-site group, select shots which started occupied and count a success
# when that same site was occupied in the final image.  Image and ROI indices in the
# dnamic-toolkit condition language are zero-based.
SURVIVAL_PROBABILITY = ConditionalProbability(
    result_prefix="survival",
    event="1[0]",
    given="0[0]",
)


# Declare single shot for experiment:
class CsTrapDropShot(ExpFragment):
    """Load and image Cs, cool, release and recapture, then image again."""

    def build_fragment(self):
        # Anything that contains @kernel needs the core device
        self.setattr_device("core")
        self.core: Core  # Type attributes help editor

        # LabEnvironment owns the RTIO hardware and its initial/safe lifecycle.
        self.setattr_fragment("environment", LabEnvironment)
        self.environment: LabEnvironment

        # Get sequence parts we need:
        self.setattr_fragment(
            "load_cs",
            LoadCsMOTToTweezers,
            hardware=self.environment.hardware,  # Pass in hardware reference.
        )
        self.load_cs: LoadCsMOTToTweezers

        self.setattr_fragment(
            "image_cs",
            CoolAndImageCsAtoms,
            hardware=self.environment.hardware,
        )
        self.image_cs: CoolAndImageCsAtoms

        # This is deliberately a separate cooling profile from the cooling performed
        # before either image.  Its duration, trap depth, shims, frequencies and DDS
        # amplitudes can therefore all be scanned without changing image acquisition.
        self.setattr_fragment(
            "cool_before_release",
            CsCoolingStage,
            hardware=self.environment.hardware,
        )
        self.cool_before_release: CsCoolingStage

        self.setattr_fragment(
            "drop_hot_atoms",
            FastTrapDrop,
            hardware=self.environment.hardware,
        )
        self.drop_hot_atoms: FastTrapDrop

        # Set image readout service up with metadata
        self.setattr_fragment(
            "image_readout",
            AtomImageReadout,
            slots=(INITIAL_TWEEZER_IMAGE, FINAL_TWEEZER_IMAGE),
            conditional_probabilities=(SURVIVAL_PROBABILITY,),
        )
        self.image_readout: AtomImageReadout

    def get_default_analyses(self):
        # For a no-axis scan, image_readout turns the repeated binary results into
        # ordinary and conditional probabilities.
        return self.image_readout.get_default_analyses()

    @kernel
    def run_once(self):
        # Synchronous RPC: invalidate old images and start acquisition if needed.
        self.image_readout.begin_shot()

        # The RPC takes variable time, so restore RTIO scheduling margin afterwards.
        self.core.break_realtime()
        delay(1.0 * ms)

        # Load Cs and finish in the molasses state (cool+repump still on)
        self.load_cs.run(turn_light_off=False)

        # Cool the atoms, taking exposure 0 and turn off cs light at the end
        self.image_cs.run(
            turn_light_on=False,
            turn_light_off=True,
        )

        # The first image leaves the atoms in a dark 1066 nm hold.  Establish every
        # cooling setting explicitly, pulse the resonant light, and return to dark.
        self.cool_before_release.run(
            turn_light_on=True,
            turn_light_off=True,
        )
        delay(10*ms)
        
        # Drop the 1066 trap
        self.drop_hot_atoms.run()
        delay(10 * us)  # Re-settle servo

        # This transition first restores image_cs.cooling.tweezer_setpoint, then turns
        # the cooling/repump light back on and takes exposure 1.
        self.image_cs.run(
            turn_light_on=True,
            turn_light_off=True,
        )

        # Move to safe state (will also drop the traps, importantly)
        self.environment.hardware.set_safe()

        # RPC to get the camera images accumulated in the camera buffer.
        # This also pushes images and derived binary occupations to result channels.
        self.image_readout.wait_read_all()


# This is a generated ExpFragment class, not an experiment instance.  It owns the
# standard no-axis child scan which repeats CsParametricHeatingShot and publishes all
# initial, final and conditional statistics as one higher-level result point.
CsTrapDropShotStatistics = make_repeated_image_shot_statistics(
    CsTrapDropShot,
    default_shots_per_point=SHOTS_PER_POINT,
)

CsTrapDropShotStatisticsExp = make_fragment_prepared_dashboard_scan_exp(
    CsTrapDropShotStatistics,
    max_rtio_underflow_retries=0,
)
