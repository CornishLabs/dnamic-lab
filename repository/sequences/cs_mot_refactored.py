"""Cs MOT-to-tweezers shots, result publication, scans, and optimisation recipes.

The reusable hardware, settings, stages, and experimental operations live in
``repository.sequences.parts.cs_mot``.  This file intentionally stays at the recipe
level: it composes those parts into a concrete camera shot and adds statistical and
Bayesian-optimisation wrappers.
"""

from artiq.coredevice.core import Core
from artiq.experiment import kernel
from artiq.language.core import delay
from artiq.language.units import MHz, V, ms

from ndscan.define.fragment import ExpFragment
from ndscan.define.parameters import IntParam, IntParamHandle
from ndscan.define.result_channels import FloatChannel
from ndscan.runtime.api import (
    ExecutionPolicy,
    ScanRequest,
    make_fragment_prepared_dashboard_scan_exp,
    make_fragment_prepared_scan_exp,
    prepare_child_scan,
)

from repository.sequences.parts.lab_hardware import LabEnvironment
from repository.sequences.parts.cs_mot import (
    CoolAndImageCsAtoms,
    LoadCsMOTToTweezers,
)
from repository.sequences.parts.imaging import (
    CS_TWEEZER_IMAGE,
    AtomImageReadout,
    TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT,
    TWEEZER_AVERAGE_PROBABILITY_RESULT,
)


# -----------------------------------------------------------------------------
# Experiment-specific defaults and result names
# -----------------------------------------------------------------------------

SHOTS_PER_POINT = 50
LOADING_GOODNESS_FIXED_OVERHEAD_MS = 600.0
TWEEZER_LOADING_GOODNESS_RESULT = "tweezer_loading_goodness"
TWEEZER_LOADING_GOODNESS_ERROR_RESULT = "tweezer_loading_goodness_error"


# -----------------------------------------------------------------------------
# Complete shot recipe used as an ndscan experiment
# -----------------------------------------------------------------------------


class LoadCsMOTToTweezersImageRefactored(ExpFragment):
    """Run the refactored Cs load/cool/image sequence once."""

    def build_fragment(self):
        # The prepared runtime determines which core compiles ``run_once`` by looking
        # for this attribute directly on the ExpFragment.  The physical Core object is
        # the same one also used by ``self.environment.hardware``; this reference
        # does not create another hardware lifecycle owner.
        self.setattr_device("core")
        self.core: Core

        # The environment is the one lifecycle owner.  Before every shot it establishes
        # the shared safe boundary, and on scan exit/pause it returns there again.
        self.setattr_fragment("environment", LabEnvironment)
        self.environment: LabEnvironment
        self.setattr_fragment(
            "load_cs_mot_to_tweezers",
            LoadCsMOTToTweezers,
            hardware=self.environment.hardware,
        )
        self.load_cs_mot_to_tweezers: LoadCsMOTToTweezers
        self.setattr_fragment(
            "cool_and_image_atoms",
            CoolAndImageCsAtoms,
            hardware=self.environment.hardware,
        )
        self.cool_and_image_atoms: CoolAndImageCsAtoms
        self.setattr_fragment(
            "image_readout",
            AtomImageReadout,
            slots=(CS_TWEEZER_IMAGE,),
        )
        self.image_readout: AtomImageReadout

    def get_default_analyses(self):
        # ndscan asks the scanned ExpFragment for its analyses rather than traversing
        # ordinary children automatically.  The shared imaging part owns the actual
        # calculation; the shot only exposes it.
        return self.image_readout.get_default_analyses()

    @kernel
    def rtio_events(self):
        self.core.break_realtime()
        delay(20.0 * ms)

        # The top-level sequence is now a readable composition of two reusable parts.
        self.load_cs_mot_to_tweezers.run()
        self.cool_and_image_atoms.run_from_molasses()

        self.environment.hardware.set_cs_tweezer_servo_enabled(0)
        delay(5.0 * ms)
        # Do not leave non-zero shim demands or configured outputs active while the
        # host waits for and processes the camera frame. Lifecycle cleanup repeats
        # this operation safely after run_once() returns.
        self.environment.hardware.set_safe()

    @kernel
    def run_once(self):
        self.image_readout.begin_shot()
        self.core.break_realtime()
        self.rtio_events()
        self.image_readout.wait_read_all()


LoadCsMOTToTweezersImageRefactoredExp = make_fragment_prepared_dashboard_scan_exp(
    LoadCsMOTToTweezersImageRefactored,
    max_rtio_underflow_retries=0,
)


# -----------------------------------------------------------------------------
# Repeated-shot statistics wrapper
# -----------------------------------------------------------------------------


class LoadCsMOTToTweezersImageStatisticsRefactored(ExpFragment):
    """Repeat the refactored shot and publish one probability point."""

    def build_fragment(self):
        self.setattr_fragment(
            "shot",
            LoadCsMOTToTweezersImageRefactored,
            detached=True,
        )
        self.shot: LoadCsMOTToTweezersImageRefactored

        self.repeat_scan = prepare_child_scan(
            self,
            self.shot,
            name="repeat_scan",
            max_rtio_underflow_retries=0,
        )

        self.shots_per_point = self.setattr_param(
            "shots_per_point",
            IntParam,
            "Raw shots to average into one scan point",
            SHOTS_PER_POINT,
            min=1,
        )
        self.shots_per_point: IntParamHandle

        self._stat_channels = self.shot.image_readout.make_statistics_channels(
            self.setattr_result
        )
        self.loading_goodness = self.setattr_result(
            TWEEZER_LOADING_GOODNESS_RESULT,
            FloatChannel,
            "Average loading probability / ((MOT load time in ms + 600) * "
            "tweezer setpoint)",
            min=0.0,
        )
        self.loading_goodness_error = self.setattr_result(
            TWEEZER_LOADING_GOODNESS_ERROR_RESULT,
            FloatChannel,
            "Propagated statistical error of the loading-goodness objective",
            min=0.0,
            display_hints={"error_bar_for": self.loading_goodness.path},
        )

    def run_once(self):
        num_shots = int(self.shots_per_point.get())
        self.repeat_scan.configure(
            ScanRequest.single(
                execution_policy=ExecutionPolicy(
                    max_points_per_batch=min(num_shots, 16)
                )
            ).with_repeats(repeats=num_shots)
        )
        outputs = self.repeat_scan.execute()
        for name, channel in self._stat_channels.items():
            channel.push(outputs[name])

        mot = self.shot.load_cs_mot_to_tweezers.mot
        mot_load_time_ms = mot.duration.get() / ms
        tweezer_setpoint = mot.tweezer_setpoint.get()
        goodness_denominator = (
            mot_load_time_ms + LOADING_GOODNESS_FIXED_OVERHEAD_MS
        ) * tweezer_setpoint
        if goodness_denominator <= 0.0:
            raise ValueError(
                "Loading goodness requires a positive MOT time plus overhead and "
                "a positive tweezer setpoint"
            )

        # Timing and setpoint are treated as exact here.  Dividing the probability by
        # a known constant therefore divides its standard error by the same constant.
        self.loading_goodness.push(
            outputs[TWEEZER_AVERAGE_PROBABILITY_RESULT] / goodness_denominator
        )
        self.loading_goodness_error.push(
            outputs[TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT] / goodness_denominator
        )


class LoadCsMOTToTweezersImageStatisticsRefactoredDashboard(
    LoadCsMOTToTweezersImageStatisticsRefactored
):
    """Dashboard wrapper exposing the most frequently adjusted settings."""

    def get_always_shown_params(self):
        shown = super().get_always_shown_params()
        load = self.shot.load_cs_mot_to_tweezers
        readout = self.shot.cool_and_image_atoms
        shown += [
            self.shots_per_point,
            load.mot.duration,
            load.mot.tweezer_setpoint,
            load.molasses.duration,
            readout.cooling.duration,
            readout.cooling.tweezer_setpoint,
            readout.cooling.light.cool_frequency,
            readout.cooling.light.cool_dds_amp,
            readout.imaging.exposure_time,
            self.shot.image_readout.camera_timeout,
        ]
        return shown


LoadCsMOTToTweezersImageStatisticsRefactoredExp = (
    make_fragment_prepared_dashboard_scan_exp(
        LoadCsMOTToTweezersImageStatisticsRefactoredDashboard,
        max_rtio_underflow_retries=0,
    )
)


# -----------------------------------------------------------------------------
# Bayesian optimisation of Cs loading
# -----------------------------------------------------------------------------

# This is deliberately a code-first optimisation recipe rather than another dashboard
# option.  Bounds are experimental safety decisions, so keep them here, near the axes
# they constrain, and review them before the first hardware run.
#
# This first-stage recipe varies only MOT controls: duration, cooling-light frequency
# and amplitude, three shims, quad demand, and tweezer depth.  The tweezer setpoint is
# established by the MOT and naturally remains active through molasses.  All explicit
# molasses settings, and both stages' repump settings, remain at their declared
# defaults so that optimisation can proceed one experimental stage at a time.
#
# These bounds are deliberately written out by name.  In a high-dimensional scan it
# is tempting to generate every range as ``default +/- x``, but explicit limits make
# the actual region sent to the hardware reviewable.  They are experimental safety
# decisions, not properties of the optimiser, so review them before running.
CS_LOADING_BO_MOT_BOUNDS = {
    "duration_ms": (50.0, 500.0),
    "cool_frequency_mhz": (97.0, 102.0),
    "cool_amplitude": (0.50, 0.66),
    "ew_setpoint_v": (-0.26, 0.34),
    "ud_setpoint_v": (0.079, 0.679),
    "ns_setpoint_v": (-0.637, -0.037),
    "quad_setpoint_v": (8.0, 9.4),
    "tweezer_setpoint": (5.8, 7.0),
}

CS_LOADING_BO_BATCH_SIZE = 3
# Two random points per dimension give this eight-dimensional first-stage search a
# useful initial design without spending most of the hour before BO begins.
# The source-default point below is additional to these random design points.
CS_LOADING_BO_INITIAL_RANDOM_POINTS = 16
# 1 baseline + 16 random settings + 12 batches of 3 BO settings = 53 settings.
# At 50 shots each and a uniformly distributed 50--500 ms MOT time, the simple
# (MOT time + 600 ms) estimate is about 50 minutes before optimiser overhead.
CS_LOADING_BO_MAX_BATCHES = 12
CS_LOADING_BO_EXPLORATION_EVERY_BATCHES = 5


def _make_cs_loading_bo_request(
    fragment: LoadCsMOTToTweezersImageStatisticsRefactored,
) -> ScanRequest:
    """Optimise loading goodness over MOT-stage controls.

    Each outer point runs ``shots_per_point`` complete camera shots through the
    prepared child scan.  The optimiser maximises average loading probability divided
    by ``(MOT duration in ms + 600) * tweezer setpoint``.  The probability's standard
    error is divided by the same known denominator and supplied as observation noise.

    The first point is assembled from the declared parameter defaults. MOT duration is
    defensively clipped to the requested bounds so that a future change to the normal
    sequence default cannot create an invalid optimiser seed.

    Every fifth BO batch also contains scan-like exploration points from the same
    strategy used by ``prepared_scan_bayesian_optimisation.py``: an uncovered-space
    point plus local axis/pair scans around the current GP optimum.  These exploratory
    observations are fed back to the same GP, so no acquired loading data is wasted.
    """
    # Keep the optional torch/gpytorch/NUBO dependency local to this experiment.  The
    # ordinary Cs loading experiments can still be examined and run if that optional
    # optimisation stack is not installed.
    try:
        from ndscan.scan import AskTellOptimiserPointPolicy
        from ndscan.scan.mapping import ParameterMapping, ScanVariable
        from ndscan.scan.optimisation import (
            CompositeExplorationStrategy,
            LocalLengthscaleExplorationStrategy,
            MhcsExplorationStrategy,
            NuboBatchBayesianOptimisationBackend,
            ScheduledExplorationStrategy,
            extract_scalar_channel_objective,
        )
    except ModuleNotFoundError as exc:
        raise ImportError(
            "Cs loading Bayesian optimisation requires torch, gpytorch, and nubo"
        ) from exc

    loading = fragment.shot.load_cs_mot_to_tweezers
    mot = loading.mot

    def default_value(handle):
        """Evaluate a parameter default without requiring an attached store.

        ARTIQ constructs this request while examining the repository, before ndscan's
        ``init_params()`` phase.  ``handle.get()`` therefore cannot be used here; the
        handle has a parameter definition but deliberately has no live value store
        yet.  Evaluating the definition also continues to work for dataset-backed
        defaults, unlike copying the numerical constants into this recipe.
        """
        return handle.parameter.eval_default(fragment.get_dataset)

    # ScanVariable is useful for frequencies because the real NDScan parameters are
    # stored in Hz.  Keeping the BO coordinates in MHz makes the recorded axes easy to
    # read; ParameterMapping performs the unit conversion before each shot.
    parameter_mappings = []

    mot_duration_ms = ScanVariable(
        "mot_duration_ms",
        description="MOT loading duration in ms",
    )
    parameter_mappings.append(
        ParameterMapping.single_target(
            mot.duration,
            [mot_duration_ms],
            lambda values: values[mot_duration_ms] * ms,
            description="Convert the BO MOT loading duration from ms to seconds",
        )
    )

    def make_frequency_axis(name, description, target):
        axis = ScanVariable(name, description=description)
        parameter_mappings.append(
            ParameterMapping.single_target(
                target,
                [axis],
                lambda values, axis=axis: values[axis] * MHz,
                description=f"Convert {description} from MHz to Hz",
            )
        )
        return axis

    mot_cool_frequency_mhz = make_frequency_axis(
        "mot_cool_frequency_mhz",
        "MOT cooling-light AOM frequency in MHz",
        mot.light.cool_frequency,
    )
    # Put axis, bounds, and baseline beside one another.  Their order must agree for a
    # multidimensional optimiser; this table-like form makes an ordering mistake much
    # harder than maintaining three separate eight-element lists.
    duration_lower_ms, duration_upper_ms = CS_LOADING_BO_MOT_BOUNDS["duration_ms"]
    declared_duration_ms = default_value(mot.duration) / ms
    # Keep this defensive clipping even though the current 300 ms default is inside the
    # BO region. It prevents a later ordinary-sequence change from feeding an invalid
    # seed to the GP.
    initial_duration_ms = min(
        max(declared_duration_ms, duration_lower_ms), duration_upper_ms
    )
    axis_specs = (
        (
            mot_duration_ms,
            duration_lower_ms,
            duration_upper_ms,
            initial_duration_ms,
        ),
        (
            mot_cool_frequency_mhz,
            *CS_LOADING_BO_MOT_BOUNDS["cool_frequency_mhz"],
            default_value(mot.light.cool_frequency) / MHz,
        ),
        (
            mot.light.cool_dds_amp,
            *CS_LOADING_BO_MOT_BOUNDS["cool_amplitude"],
            default_value(mot.light.cool_dds_amp),
        ),
        (
            mot.shims.ew_setpoint,
            *CS_LOADING_BO_MOT_BOUNDS["ew_setpoint_v"],
            default_value(mot.shims.ew_setpoint) / V,
        ),
        (
            mot.shims.ud_setpoint,
            *CS_LOADING_BO_MOT_BOUNDS["ud_setpoint_v"],
            default_value(mot.shims.ud_setpoint) / V,
        ),
        (
            mot.shims.ns_setpoint,
            *CS_LOADING_BO_MOT_BOUNDS["ns_setpoint_v"],
            default_value(mot.shims.ns_setpoint) / V,
        ),
        (
            mot.quad_setpoint,
            *CS_LOADING_BO_MOT_BOUNDS["quad_setpoint_v"],
            default_value(mot.quad_setpoint) / V,
        ),
        (
            mot.tweezer_setpoint,
            *CS_LOADING_BO_MOT_BOUNDS["tweezer_setpoint"],
            default_value(mot.tweezer_setpoint),
        ),
    )
    axes = tuple(spec[0] for spec in axis_specs)
    lower_bounds = [spec[1] for spec in axis_specs]
    upper_bounds = [spec[2] for spec in axis_specs]

    # Always measure the baseline first, then append the random initial design.  Apart
    # from the explicitly clipped duration above, this is the current set of declared
    # defaults and gives a recognisable comparison for the optimiser.
    initial_coordinates = [spec[3] for spec in axis_specs]
    for axis, lower, upper, value in axis_specs:
        if not lower <= value <= upper:
            raise ValueError(
                f"Initial value {value} for {axis.name!r} is outside its BO bounds "
                f"[{lower}, {upper}]"
            )
    initial_point = [initial_coordinates]

    planned_settings = (
        1
        + CS_LOADING_BO_INITIAL_RANDOM_POINTS
        + CS_LOADING_BO_BATCH_SIZE * CS_LOADING_BO_MAX_BATCHES
    )
    shots_per_setting = int(default_value(fragment.shots_per_point))
    minimum_minutes = (
        planned_settings
        * shots_per_setting
        * (duration_lower_ms + LOADING_GOODNESS_FIXED_OVERHEAD_MS)
        / 60_000.0
    )
    maximum_minutes = (
        planned_settings
        * shots_per_setting
        * (duration_upper_ms + LOADING_GOODNESS_FIXED_OVERHEAD_MS)
        / 60_000.0
    )
    midpoint_minutes = (minimum_minutes + maximum_minutes) / 2.0
    print(
        f"Cs loading BO: up to {planned_settings} settings x "
        f"{shots_per_setting} shots; estimated acquisition time "
        f"{minimum_minutes:.0f}-{maximum_minutes:.0f} min "
        f"({midpoint_minutes:.0f} min at the duration midpoint), excluding GP fit "
        "time."
    )

    exploration = ScheduledExplorationStrategy(
        every_batches=CS_LOADING_BO_EXPLORATION_EVERY_BATCHES,
        offset=CS_LOADING_BO_EXPLORATION_EVERY_BATCHES - 1,
        strategy=CompositeExplorationStrategy(
            [
                MhcsExplorationStrategy(
                    num_points=1,
                    min_normalised_distance=0.02,
                ),
                LocalLengthscaleExplorationStrategy(
                    num_points=2,
                    axis_points=5,
                    pair_points=3,
                    span_in_lengthscales=2.0,
                    min_normalised_distance=0.02,
                    surrogate_num_starts=12,
                ),
            ],
            min_normalised_distance=0.02,
        ),
    )

    backend = NuboBatchBayesianOptimisationBackend(
        bounds=[lower_bounds, upper_bounds],
        batch_size=CS_LOADING_BO_BATCH_SIZE,
        initial_points=initial_point,
        initial_design_size=CS_LOADING_BO_INITIAL_RANDOM_POINTS,
        random_seed=0,
        acquisition_name="ucb",
        fit_steps=200,
        fit_lr=0.05,
        acquisition_num_starts=6,
        surrogate_num_starts=12,
        batch_mc_samples=128,
        batch_acq_lr=0.05,
        batch_acq_steps=150,
        batch_ucb_beta=1.96**2,
        max_batches=CS_LOADING_BO_MAX_BATCHES,
        minimise=False,
        min_normalised_distance=1e-6,
        exploration_strategy=exploration,
    )

    return ScanRequest(
        axes=axes,
        point_policy=AskTellOptimiserPointPolicy(
            backend,
            # The statistics fragment registers its six probability/statistics
            # channels first, followed by goodness and its error as channels 6 and 7.
            extract_scalar_channel_objective(
                "channel_6",
                noise_channel_key="channel_7",
                metadata={
                    "objective": TWEEZER_LOADING_GOODNESS_RESULT,
                    "noise": TWEEZER_LOADING_GOODNESS_ERROR_RESULT,
                },
            ),
        ),
        execution_policy=ExecutionPolicy(max_points_per_batch=CS_LOADING_BO_BATCH_SIZE),
        parameter_mappings=tuple(parameter_mappings),
        metadata={
            "experiment": "cs_mot_loading_bayesian_optimisation",
            "objective": "maximise_tweezer_loading_goodness",
            "planned_settings": planned_settings,
            "estimated_acquisition_minutes_midpoint": midpoint_minutes,
            "estimated_acquisition_minutes_minimum": minimum_minutes,
            "estimated_acquisition_minutes_maximum": maximum_minutes,
        },
    )


OptimiseCsMOTLoadingRefactoredExp = make_fragment_prepared_scan_exp(
    LoadCsMOTToTweezersImageStatisticsRefactored,
    _make_cs_loading_bo_request,
    max_rtio_underflow_retries=0,
)
