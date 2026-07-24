"""Atom experiment: two-stage Cs MOT loading and Bayesian optimisation.

The physical shot is deliberately short to read: establish a bulk MOT, move through
an independently parameterised compressed-MOT stage, enter the existing molasses
stage, then cool and image.  Repump, molasses, cooling and imaging settings remain at
their normal defaults in this first optimisation.

The optimiser varies fifteen coordinates: duration, cooling frequency, cooling DDS
amplitude, three shim values, and quad demand for each MOT stage, plus one 1066-nm
setpoint shared by both.

This is an exploit-first follow-up to RID 8413.  It starts by remeasuring a few known
working points from that run (plus two timing variants of its best point), then uses
expected improvement with the standard backend settings.  There is no new global
random design or additional exploration policy; diagnostic lines through the optimum
can be run separately afterwards.
"""

from artiq.coredevice.core import Core
from artiq.experiment import kernel
from artiq.language.core import delay
from artiq.language.units import MHz, V, ms, s

from ndscan.define.fragment import ExpFragment
from ndscan.define.result_channels import FloatChannel
from ndscan.runtime.api import (
    ExecutionPolicy,
    ScanRequest,
    make_fragment_prepared_dashboard_scan_exp,
    make_fragment_prepared_scan_exp,
)

from repository.sequences.parts.cs_mot import (
    CoolAndImageCsAtoms,
    LoadTwoStageCsMOTToTweezers,
)
from repository.sequences.parts.imaging import (
    CS_TWEEZER_IMAGE,
    AtomImageReadout,
    TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT,
    TWEEZER_AVERAGE_PROBABILITY_RESULT,
)
from repository.sequences.parts.lab_hardware import LabEnvironment
from repository.sequences.parts.repetition import (
    make_repeated_image_shot_statistics,
)


# -----------------------------------------------------------------------------
# Shot and objective
# -----------------------------------------------------------------------------

SHOTS_PER_POINT = 50

# This represents approximately fixed work around the variable MOT stages: molasses,
# cooling, imaging, camera transfer and between-shot overhead.  It stops the optimiser
# preferring an unhelpfully long or deep MOT merely for a tiny increase in loading
# probability.
LOADING_GOODNESS_FIXED_OVERHEAD_MS = 600.0
# Multiplying the objective by a positive constant cannot move its optimum.  It does
# put the GP signal and its measured uncertainty near order unity, safely above
# GPyTorch's fixed-noise numerical floor.  Keep the error channel on the same scale.
LOADING_GOODNESS_GP_SCALE = 1.0e5
LOADING_GOODNESS_RESULT = "tweezer_loading_goodness"
LOADING_GOODNESS_ERROR_RESULT = "tweezer_loading_goodness_error"


class CsTwoStageMOTLoadingShot(ExpFragment):
    """Load Cs with two MOT stages, then cool and image once."""

    def build_fragment(self):
        # Prepared-runtime kernels need the core device declared on the shot itself.
        self.setattr_device("core")
        self.core: Core

        # LabEnvironment is the single owner of RTIO initialisation and the safe
        # before/after-shot state.  Experimental parts only borrow its hardware.
        self.setattr_fragment("environment", LabEnvironment)
        self.environment: LabEnvironment
        self.setattr_fragment(
            "load_cs",
            LoadTwoStageCsMOTToTweezers,
            hardware=self.environment.hardware,
        )
        self.load_cs: LoadTwoStageCsMOTToTweezers
        self.setattr_fragment(
            "image_cs",
            CoolAndImageCsAtoms,
            hardware=self.environment.hardware,
        )
        self.image_cs: CoolAndImageCsAtoms
        self.setattr_fragment(
            "image_readout",
            AtomImageReadout,
            slots=(CS_TWEEZER_IMAGE,),
        )
        self.image_readout: AtomImageReadout

    def get_default_analyses(self):
        # The shared readout turns the repeated binary occupations into per-tweezer
        # and average loading probabilities, with their binomial uncertainties.
        return self.image_readout.get_default_analyses()

    @kernel
    def run_once(self):
        self.image_readout.begin_shot()

        # begin_shot() is an RPC with variable duration; rebuild RTIO slack before
        # issuing the deterministic sequence of hardware events.
        self.core.break_realtime()
        delay(20.0 * ms)

        self.load_cs.run(turn_light_off=False)
        self.image_cs.run(
            turn_light_on=False,
            turn_light_off=True,
        )

        # Match the established Cs loading shot: stop the loop, allow its output to
        # settle, then return the complete apparatus to the shared safe state before
        # waiting for the host-side camera readout.
        self.environment.hardware.set_cs_tweezer_servo_enabled(0)
        delay(5.0 * ms)
        self.environment.hardware.set_safe()
        self.image_readout.wait_read_all()


# The factory supplies the usual no-axis child scan and copies all standard image
# statistics into the outer point.  The small subclass adds only the scalar objective
# needed by the GP; it does not recalculate any image statistics.
_CsTwoStageMOTStatistics = make_repeated_image_shot_statistics(
    CsTwoStageMOTLoadingShot,
    default_shots_per_point=SHOTS_PER_POINT,
    class_name="CsTwoStageMOTStatisticsBase",
)


class CsTwoStageMOTStatistics(_CsTwoStageMOTStatistics):
    """Repeat the shot and publish a time-normalised loading objective."""

    def build_fragment(self):
        super().build_fragment()
        self.loading_goodness = self.setattr_result(
            LOADING_GOODNESS_RESULT,
            FloatChannel,
            "1e5 * average loading probability / ((bulk MOT ms + compressed MOT "
            "ms + 600) * tweezer setpoint)",
            min=0.0,
        )
        self.loading_goodness_error = self.setattr_result(
            LOADING_GOODNESS_ERROR_RESULT,
            FloatChannel,
            "Statistical error of the time-normalised loading objective",
            min=0.0,
            display_hints={"error_bar_for": self.loading_goodness.path},
        )

    def publish_derived_statistics(self, outputs):
        loading = self.shot.load_cs
        variable_time_ms = (
            loading.bulk_mot.duration.get() + loading.compressed_mot.duration.get()
        ) / ms
        tweezer_setpoint = loading.tweezer_setpoint.get()
        denominator = (
            variable_time_ms + LOADING_GOODNESS_FIXED_OVERHEAD_MS
        ) * tweezer_setpoint
        if denominator <= 0.0:
            raise ValueError(
                "Loading goodness requires a positive total shot time and tweezer "
                "setpoint"
            )

        # Durations and setpoint are treated as exact, so the probability and its
        # standard error are divided by precisely the same deterministic number.
        self.loading_goodness.push(
            LOADING_GOODNESS_GP_SCALE
            * outputs[TWEEZER_AVERAGE_PROBABILITY_RESULT]
            / denominator
        )
        self.loading_goodness_error.push(
            LOADING_GOODNESS_GP_SCALE
            * outputs[TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT]
            / denominator
        )


# A normal dashboard entry for checking the complete measurement before committing
# to the long optimisation.  Submit it with no axes selected: it runs the default 50
# shots at the exact baseline, then publishes loading probabilities and goodness.
CsTwoStageMOTLoadingCheckExp = make_fragment_prepared_dashboard_scan_exp(
    CsTwoStageMOTStatistics,
    max_rtio_underflow_retries=0,
)
CsTwoStageMOTLoadingCheckExp.__doc__ = (
    "Check two-stage Cs MOT loading once at the dashboard settings."
)


# -----------------------------------------------------------------------------
# Fifteen-dimensional follow-up GP optimisation
# -----------------------------------------------------------------------------

# These are intentionally explicit and local to the optimisation recipe: they are
# experimental search decisions, not fundamental limits of the reusable stages.
CS_BULK_MOT_BOUNDS = {
    "duration_ms": (50.0, 500.0),
    # RID 8413 found almost all useful loading below 103 MHz.  Duration stays wide:
    # in particular, we do not rule out the historically useful 100 ms region.
    "cool_frequency_mhz": (96.0, 103.0),
    "cool_amplitude": (0.40, 0.66),
    "ew_setpoint_v": (-0.26, 0.34),
    "ud_setpoint_v": (0.21, 0.81),
    "ns_setpoint_v": (-0.55, 0.05),
    "quad_setpoint_v": (7.0, 9.0),
}

# The second stage is allowed to move towards the established molasses position and
# detuning.  Its region is wider for that reason, but still lies within every stage
# parameter's declared hardware limits.
CS_COMPRESSED_MOT_BOUNDS = {
    "duration_ms": (5.0, 250.0),
    "cool_frequency_mhz": (97.0, 103.0),
    "cool_amplitude": (0.35, 0.68),
    "ew_setpoint_v": (-0.30, 0.20),
    # The high-U/D and positive-N/S corners were repeatedly unproductive.  The
    # retained ranges still contain the default and all selected working seeds.
    "ud_setpoint_v": (0.35, 0.95),
    "ns_setpoint_v": (-0.40, 0.15),
    "quad_setpoint_v": (7.0, 9.4),
}

# This is one shared coordinate rather than one per stage.  The bounds reproduce the
# useful part of the established range, while retaining the ordinary 6.567 V point.
CS_TWEEZER_SETPOINT_BOUNDS = (5.8, 6.7)

# Good measured coordinates from RID 8413, in the exact ``axis_specs`` order below
# (durations in seconds and cooling frequencies in MHz).
# Keeping the provenance and point index beside each coordinate makes these values
# auditable rather than turning them into mysterious new defaults.  They are seeds:
# every one is remeasured in the new run; no old result value is imported into its GP.
RID_8413_GOOD_SEEDS = (
    (
        "RID 8413 point 19 (best objective)",
        (
            0.45951683730791524,
            97.15831493885825,
            0.5428809341357901,
            -0.0825901255124615,
            0.5249576794251649,
            -0.4270316622920304,
            8.883080211220886,
            0.19288893657164184,
            99.36058503543006,
            0.5287816545421007,
            -0.24722894560096792,
            0.8591191079491498,
            0.01146553674984685,
            7.261799592123415,
            5.961078946481579,
        ),
    ),
    (
        "RID 8413 point 177 (54 ms bulk MOT)",
        (
            0.0543358039855957,
            96.20124816894531,
            0.4607943296432495,
            -0.13798768818378448,
            0.4429595470428467,
            0.02755010686814785,
            7.735010623931885,
            0.2032258758544922,
            97.8000259399414,
            0.564256489276886,
            0.1353016346693039,
            0.4193335771560669,
            -0.36017516255378723,
            9.240344047546387,
            6.163031101226807,
        ),
    ),
    (
        "RID 8413 point 138 (108 ms bulk MOT)",
        (
            0.10753843688964844,
            98.70791625976562,
            0.6421199440956116,
            0.19752244651317596,
            0.6233415007591248,
            -0.22251266241073608,
            8.676957130432129,
            0.23030648803710937,
            98.4144058227539,
            0.638636589050293,
            -0.19223614037036896,
            0.6318128108978271,
            -0.16724318265914917,
            7.162422180175781,
            6.066418170928955,
        ),
    ),
    (
        "RID 8413 point 254 (5 ms compressed MOT)",
        (
            0.47975997924804686,
            96.26969909667969,
            0.6562853455543518,
            0.3261096775531769,
            0.8093456625938416,
            0.03758750483393669,
            8.991008758544922,
            0.005196279525756836,
            97.34336853027344,
            0.3581710159778595,
            -0.2977440655231476,
            0.36044788360595703,
            -0.39787495136260986,
            9.374028205871582,
            5.82694673538208,
        ),
    ),
    (
        "RID 8413 point 37 (independent working seed)",
        (
            0.29128234490765903,
            102.42240637208857,
            0.5568566573817498,
            0.1832327748732283,
            0.6621623367664329,
            -0.21808481198744584,
            7.7920278937016425,
            0.1063987793195432,
            98.27693640183192,
            0.6668198440566077,
            0.02032750118528298,
            0.4728075189426445,
            -0.11102858012120542,
            9.069927654002633,
            6.067273792268369,
        ),
    ),
)

BO_BATCH_SIZE = 2
# Exact GP refits become increasingly expensive at high point counts.  This cap gives
# roughly five hundred settings, which is a useful overnight run without recreating
# the needlessly long, corner-seeking tail of RID 8413.
BO_MAX_BATCHES = 240


def _make_cs_two_stage_bo_request(
    fragment: CsTwoStageMOTStatistics,
) -> ScanRequest:
    """Build the optimisation request after the complete fragment tree exists."""
    try:
        from ndscan.scan import AskTellOptimiserPointPolicy
        from ndscan.scan.mapping import ParameterMapping, ScanVariable
        from ndscan.scan.optimisation import (
            NuboBatchBayesianOptimisationBackend,
            extract_scalar_channel_objective,
        )
    except ModuleNotFoundError as exc:
        raise ImportError(
            "Cs MOT optimisation requires the optional torch, gpytorch and nubo "
            "packages"
        ) from exc

    loading = fragment.shot.load_cs
    bulk = loading.bulk_mot
    compressed = loading.compressed_mot

    def default_value(handle):
        """Read a declared default before ndscan has attached live value stores."""
        return handle.parameter.eval_default(fragment.get_dataset)

    parameter_mappings = []

    def make_mapped_axis(name, description, target, scale, unit_name):
        """Give every BO coordinate a short, unambiguous saved axis name."""
        axis = ScanVariable(name, description=description)
        parameter_mappings.append(
            ParameterMapping.single_target(
                target,
                [axis],
                lambda values, axis=axis, scale=scale: values[axis] * scale,
                description=f"Map {description} ({unit_name}) to its stage parameter",
            )
        )
        return axis

    def stage_axis_specs(prefix, label, stage, bounds):
        """Keep axis, range and baseline together so their ordering cannot drift."""
        duration_axis = make_mapped_axis(
            f"{prefix}_duration_s",
            f"{label} duration",
            stage.duration,
            s,
            "s",
        )
        frequency_axis = make_mapped_axis(
            f"{prefix}_cool_frequency_mhz",
            f"{label} cooling AOM frequency",
            stage.light.cool_frequency,
            MHz,
            "MHz",
        )
        amplitude_axis = make_mapped_axis(
            f"{prefix}_cool_amplitude",
            f"{label} cooling DDS amplitude",
            stage.light.cool_dds_amp,
            1.0,
            "fraction",
        )
        ew_axis = make_mapped_axis(
            f"{prefix}_ew_setpoint_v",
            f"{label} E/W shim setpoint",
            stage.shims.ew_setpoint,
            V,
            "V",
        )
        ud_axis = make_mapped_axis(
            f"{prefix}_ud_setpoint_v",
            f"{label} U/D shim setpoint",
            stage.shims.ud_setpoint,
            V,
            "V",
        )
        ns_axis = make_mapped_axis(
            f"{prefix}_ns_setpoint_v",
            f"{label} N/S shim setpoint",
            stage.shims.ns_setpoint,
            V,
            "V",
        )
        quad_axis = make_mapped_axis(
            f"{prefix}_quad_setpoint_v",
            f"{label} quadrupole-coil demand",
            stage.quad_setpoint,
            V,
            "V",
        )
        return (
            (
                duration_axis,
                *(value / 1000.0 for value in bounds["duration_ms"]),
                default_value(stage.duration) / s,
            ),
            (
                frequency_axis,
                *bounds["cool_frequency_mhz"],
                default_value(stage.light.cool_frequency) / MHz,
            ),
            (
                amplitude_axis,
                *bounds["cool_amplitude"],
                default_value(stage.light.cool_dds_amp),
            ),
            (
                ew_axis,
                *bounds["ew_setpoint_v"],
                default_value(stage.shims.ew_setpoint) / V,
            ),
            (
                ud_axis,
                *bounds["ud_setpoint_v"],
                default_value(stage.shims.ud_setpoint) / V,
            ),
            (
                ns_axis,
                *bounds["ns_setpoint_v"],
                default_value(stage.shims.ns_setpoint) / V,
            ),
            (
                quad_axis,
                *bounds["quad_setpoint_v"],
                default_value(stage.quad_setpoint) / V,
            ),
        )

    stage_specs = stage_axis_specs(
        "bulk_mot",
        "Bulk-MOT",
        bulk,
        CS_BULK_MOT_BOUNDS,
    ) + stage_axis_specs(
        "compressed_mot",
        "Compressed-MOT",
        compressed,
        CS_COMPRESSED_MOT_BOUNDS,
    )
    tweezer_setpoint_axis = make_mapped_axis(
        "tweezer_setpoint_v",
        "1066-nm servo setpoint shared by both MOT stages",
        loading.tweezer_setpoint,
        V,
        "V",
    )
    axis_specs = stage_specs + (
        (
            tweezer_setpoint_axis,
            *CS_TWEEZER_SETPOINT_BOUNDS,
            default_value(loading.tweezer_setpoint) / V,
        ),
    )

    axes = tuple(spec[0] for spec in axis_specs)
    lower_bounds = [spec[1] for spec in axis_specs]
    upper_bounds = [spec[2] for spec in axis_specs]
    initial_coordinates = [spec[3] for spec in axis_specs]

    # Fail during repository examination if an edited sequence default is no longer a
    # valid seed.  Silent clipping would make the advertised baseline cease to be the
    # exact working point.
    for axis, lower, upper, value in axis_specs:
        if not lower <= value <= upper:
            raise ValueError(
                f"Default {value} for {axis.name!r} is outside its BO bounds "
                f"[{lower}, {upper}]"
            )

    # Remeasure the previous best first, then two versions which directly test the
    # experimentally motivated short-loading hypothesis.  The first keeps a 100 ms
    # bulk stage and the old compressed duration; the second uses 100 ms total MOT
    # time.  The ordinary default and four other measured working points give the
    # initial GP some independent structure without another global random design.
    best_label, best_coordinates_tuple = RID_8413_GOOD_SEEDS[0]
    best_coordinates = list(best_coordinates_tuple)
    best_with_100_ms_bulk = best_coordinates.copy()
    best_with_100_ms_bulk[0] = 0.100
    best_with_100_ms_total = best_coordinates.copy()
    best_with_100_ms_total[0] = 0.075
    best_with_100_ms_total[7] = 0.025
    labelled_initial_points = [
        (best_label, best_coordinates),
        (
            "RID 8413 best settings with 100 ms bulk MOT",
            best_with_100_ms_bulk,
        ),
        (
            "RID 8413 best settings with 100 ms total MOT time",
            best_with_100_ms_total,
        ),
        ("current sequence default", initial_coordinates),
        *((label, list(coordinates)) for label, coordinates in RID_8413_GOOD_SEEDS[1:]),
    ]

    # Check historical and derived seeds just as strictly as the defaults.  A bounds
    # edit should produce a useful examination error, not a cryptic optimiser failure
    # after the experiment has started.
    for label, coordinates in labelled_initial_points:
        if len(coordinates) != len(axis_specs):
            raise ValueError(
                f"Initial point {label!r} has {len(coordinates)} coordinates; "
                f"expected {len(axis_specs)}"
            )
        for spec, value in zip(axis_specs, coordinates, strict=True):
            axis, lower, upper, _ = spec
            if not lower <= value <= upper:
                raise ValueError(
                    f"Initial point {label!r} has {value} for {axis.name!r}, "
                    f"outside [{lower}, {upper}]"
                )

    initial_points = [coordinates for _, coordinates in labelled_initial_points]
    planned_settings = len(initial_points) + BO_BATCH_SIZE * BO_MAX_BATCHES
    shots_per_setting = int(default_value(fragment.shots_per_point))
    minimum_variable_ms = (
        CS_BULK_MOT_BOUNDS["duration_ms"][0]
        + CS_COMPRESSED_MOT_BOUNDS["duration_ms"][0]
    )
    maximum_variable_ms = (
        CS_BULK_MOT_BOUNDS["duration_ms"][1]
        + CS_COMPRESSED_MOT_BOUNDS["duration_ms"][1]
    )
    minimum_minutes = (
        planned_settings
        * shots_per_setting
        * (minimum_variable_ms + LOADING_GOODNESS_FIXED_OVERHEAD_MS)
        / 60_000.0
    )
    maximum_minutes = (
        planned_settings
        * shots_per_setting
        * (maximum_variable_ms + LOADING_GOODNESS_FIXED_OVERHEAD_MS)
        / 60_000.0
    )
    midpoint_minutes = (minimum_minutes + maximum_minutes) / 2.0
    print(
        f"Two-stage Cs MOT BO: up to {planned_settings} settings x "
        f"{shots_per_setting} shots; estimated acquisition time "
        f"{minimum_minutes:.0f}-{maximum_minutes:.0f} min "
        f"({midpoint_minutes:.0f} min at the duration midpoint), excluding GP fit "
        "time."
    )

    # Keep the overnight policy deliberately ordinary: two greedy EI suggestions per
    # fitted GP, with NUBO/ndscan's numerical defaults and no second exploration
    # policy layered on top.  Lines through the resulting optimum are more useful as
    # a separate diagnostic scan tomorrow.
    backend = NuboBatchBayesianOptimisationBackend(
        bounds=[lower_bounds, upper_bounds],
        batch_size=BO_BATCH_SIZE,
        initial_points=initial_points,
        initial_design_size=0,
        random_seed=0,
        acquisition_name="ei",
        max_batches=BO_MAX_BATCHES,
        minimise=False,
    )

    return ScanRequest(
        axes=axes,
        point_policy=AskTellOptimiserPointPolicy(
            backend,
            # One image slot creates six standard statistics channels.  The derived
            # objective and its error are consequently channels 6 and 7.
            extract_scalar_channel_objective(
                "channel_6",
                noise_channel_key="channel_7",
                metadata={
                    "objective": LOADING_GOODNESS_RESULT,
                    "noise": LOADING_GOODNESS_ERROR_RESULT,
                },
            ),
        ),
        execution_policy=ExecutionPolicy(max_points_per_batch=BO_BATCH_SIZE),
        parameter_mappings=tuple(parameter_mappings),
        metadata={
            "experiment": "cs_two_stage_mot_bayesian_optimisation",
            "objective": "maximise_time_normalised_loading_probability",
            "objective_scale": LOADING_GOODNESS_GP_SCALE,
            "optimisation_pass": "exploit_first_followup_to_rid_8413",
            "acquisition": "expected_improvement",
            "initial_point_labels": [label for label, _ in labelled_initial_points],
            "planned_settings": planned_settings,
            "estimated_acquisition_minutes_midpoint": midpoint_minutes,
            "estimated_acquisition_minutes_minimum": minimum_minutes,
            "estimated_acquisition_minutes_maximum": maximum_minutes,
        },
    )


OptimiseCsTwoStageMOTLoadingExp = make_fragment_prepared_scan_exp(
    CsTwoStageMOTStatistics,
    _make_cs_two_stage_bo_request,
    max_rtio_underflow_retries=0,
)
OptimiseCsTwoStageMOTLoadingExp.__doc__ = (
    "Optimise two-stage Cs MOT loading with Gaussian-process optimisation."
)
