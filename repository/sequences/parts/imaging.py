"""Shared camera acquisition, live slots, ROI processing, and image statistics.

An experiment owns one :class:`AtomImageReadout` fragment and declares the images it
expects as :class:`ImageSlot` values.  The fragment configures the Andor once,
invalidates all live slots together at the start of a shot, and turns every acquired
image into the same four saved results: image, ROI counts, binary occupations, and
applied thresholds.

The camera sensor crop is fixed for the whole experiment.  The small analysis ROIs are
different: each image slot has its own defaults, published under ``live.imaging``, and
the viewer may move them while the experiment is running.
"""

from dataclasses import dataclass

import numpy as np

from dnamic_toolkit.imaging.binomial import estimate_probability_array
from dnamic_toolkit.imaging.conditions import (
    conditional_binomial,
    parse_condition_syntax,
)
from dnamic_toolkit.imaging.rois import sum_counts_in_rois
from dnamic_toolkit.imaging.rois import threshold_counts_to_occupancy

from artiq.experiment import rpc
from artiq.language.units import ms, s

from ndscan.define.default_analysis import AnalysisFeedback, CustomAnalysis
from ndscan.define.fragment import Fragment
from ndscan.define.parameters import FloatParam, FloatParamHandle
from ndscan.define.result_channels import ArrayChannel, FloatChannel, IntChannel


# -----------------------------------------------------------------------------
# One camera configuration for all current atom-imaging experiments
# -----------------------------------------------------------------------------

CAMERA_TEMP_C = -60
CAMERA_GAIN = 100
CAMERA_OUTPUT_AMPLIFIER = 0
CAMERA_AD_CHANNEL = 0
CAMERA_HSSPEED_INDEX = 2
CAMERA_VSSPEED_INDEX = 4
CAMERA_PREAMP_GAIN_INDEX = 2
CAMERA_EXPOSURE_TIME = 30.0 * ms
CAMERA_TIMEOUT = 20.0 * s
CAMERA_FAST_EXT_TRIGGER = True

# The Andor SDK uses inclusive full-sensor coordinates in (x0, x1, y0, y1)
# order.  This crop is the union of the established Rb and Cs crops.  Keeping one
# configuration means the camera is never reconfigured in the middle of a shot.
CAMERA_ROI = (111, 190, 195, 265)
CAMERA_IMAGE_SHAPE = (
    CAMERA_ROI[3] - CAMERA_ROI[2] + 1,
    CAMERA_ROI[1] - CAMERA_ROI[0] + 1,
)  # NumPy images are (y, x), here (71, 80).

ROI_RESULT_DIM_NAMES = ("group", "roi")
LIVE_EXPECTED_IMAGES_DATASET = "live.imaging.expected_images"


# -----------------------------------------------------------------------------
# Established analysis-ROI layouts in coordinates of the union camera crop
# -----------------------------------------------------------------------------

RB_NUM_TWEEZER_ROIS = 5
RB_TWEEZER_ROIS = (
    (
        (60, 63, 16, 19),
        (47, 50, 16, 19),
        (32, 35, 16, 19),
        (18, 21, 16, 19),
        (4, 7, 16, 19),
    ),
)
# This is the current useful default for both single- and dual-species Rb readout. It
# remains editable through the live datasets and should eventually be replaced by a
# threshold obtained from calibrated empty/loaded histograms.
RB_TWEEZER_THRESHOLDS = (tuple(9000.0 for _ in range(RB_NUM_TWEEZER_ROIS)),)

CS_NUM_TWEEZER_ROIS = 9
CS_ROI_SPACING = 7.25
CS_TWEEZER_ROIS = (
    tuple(
        (
            round(258 - CS_ROI_SPACING * i) - CAMERA_ROI[2],
            round(258 - CS_ROI_SPACING * i) + 3 - CAMERA_ROI[2],
            171 - CAMERA_ROI[0],
            174 - CAMERA_ROI[0],
        )
        for i in range(CS_NUM_TWEEZER_ROIS)
    ),
)
CS_TWEEZER_THRESHOLDS = (tuple(9000.0 for _ in range(CS_NUM_TWEEZER_ROIS)),)


@dataclass(frozen=True)
class ImageSlot:
    """Build-time description of one expected image and its occupation analysis.

    ``result_prefix`` is the singular stem used by all saved channels. For example,
    ``"tweezer"`` produces ``tweezer_image`` and ``tweezer_roi_counts``;
    ``"rb_tweezer"`` produces the corresponding ``rb_`` channels.
    """

    result_prefix: str
    rois: tuple
    thresholds: tuple

    def __post_init__(self):
        if not self.result_prefix:
            raise ValueError("An image slot requires a non-empty result prefix")

        rois = np.asarray(self.rois)
        thresholds = np.asarray(self.thresholds)
        if rois.ndim != 3 or rois.shape[-1] != 4:
            raise ValueError(
                "Image-slot ROIs must have shape (groups, rois, 4), "
                f"not {rois.shape}"
            )
        if thresholds.shape != rois.shape[:-1]:
            raise ValueError(
                "Image-slot thresholds must have one value per ROI: expected "
                f"{rois.shape[:-1]}, got {thresholds.shape}"
            )

    @property
    def result_shape(self):
        rois = np.asarray(self.rois)
        return tuple(rois.shape[:-1])

    @property
    def label(self):
        return self.result_prefix.replace("_", " ").capitalize()

    def result_name(self, suffix):
        return f"{self.result_prefix}_{suffix}"


@dataclass(frozen=True)
class ConditionalProbability:
    """One named ``P(event | given)`` derived from the shot's image occupations.

    ``event`` and ``given`` use the compact condition language from
    :mod:`dnamic_toolkit.imaging.conditions`.  Image and ROI indices are zero-based;
    for example, ``event="1[0]", given="0[0]"`` means "ROI 0 is occupied in
    image 1, given that ROI 0 was occupied in image 0".

    Conditions are evaluated independently for each ROI *group*.  On every shot,
    ``given`` produces the Boolean which decides whether that shot enters the
    denominator.  A selected shot enters the numerator only when ``event`` is also
    true.  Empty ``given`` syntax means every shot is selected.
    """

    result_prefix: str
    event: str
    given: str = ""

    def __post_init__(self):
        if not self.result_prefix:
            raise ValueError(
                "A conditional probability requires a non-empty result prefix"
            )
        # Parse here as well as during readout construction so syntax errors are
        # reported next to the declaration rather than after an experiment starts.
        parse_condition_syntax(self.event)
        parse_condition_syntax(self.given)

    @property
    def label(self):
        return self.result_prefix.replace("_", " ").capitalize()

    def result_name(self, suffix):
        """Return the saved channel name for one statistic of this condition."""
        return f"{self.result_prefix}_{suffix}"


# Ready-made declarations for the three migrated experiments.  Future experiments can
# declare any number of ImageSlot values using the same Rb/Cs ROI presets.
CS_TWEEZER_IMAGE = ImageSlot(
    "tweezer",
    CS_TWEEZER_ROIS,
    CS_TWEEZER_THRESHOLDS,
)
RB_TWEEZER_IMAGE = ImageSlot(
    "tweezer",
    RB_TWEEZER_ROIS,
    RB_TWEEZER_THRESHOLDS,
)
DUAL_RB_TWEEZER_IMAGE = ImageSlot(
    "rb_tweezer",
    RB_TWEEZER_ROIS,
    RB_TWEEZER_THRESHOLDS,
)
DUAL_CS_TWEEZER_IMAGE = ImageSlot(
    "cs_tweezer",
    CS_TWEEZER_ROIS,
    CS_TWEEZER_THRESHOLDS,
)


@dataclass(frozen=True)
class _ImageResultChannels:
    image: ArrayChannel
    counts: ArrayChannel
    bright: ArrayChannel
    thresholds: ArrayChannel


def _statistics_result_names(slot):
    return {
        "probability": slot.result_name("roi_bright_probability"),
        "probability_error": slot.result_name("roi_bright_probability_error"),
        "average": slot.result_name("average_bright_probability"),
        "average_error": slot.result_name("average_bright_probability_error"),
        "num_shots": slot.result_name("roi_num_shots"),
        "num_bright": slot.result_name("roi_num_bright"),
    }


def _conditional_probability_result_names(spec):
    return {
        "probability_by_group": spec.result_name("probability_by_group"),
        "probability_error_by_group": spec.result_name(
            "probability_error_by_group"
        ),
        "num_selected_by_group": spec.result_name("num_selected_by_group"),
        "num_successes_by_group": spec.result_name("num_successes_by_group"),
        "probability": spec.result_name("probability"),
        "probability_error": spec.result_name("probability_error"),
        "num_selected": spec.result_name("num_selected"),
        "num_successes": spec.result_name("num_successes"),
    }


# These names are used by the existing single-species statistics/BO wrappers.  Keeping
# them here makes the shared imaging schema the source of truth.
_SINGLE_TWEEZER_STATISTICS_NAMES = _statistics_result_names(CS_TWEEZER_IMAGE)
TWEEZER_ROI_PROBABILITY_RESULT = _SINGLE_TWEEZER_STATISTICS_NAMES["probability"]
TWEEZER_ROI_PROBABILITY_ERROR_RESULT = _SINGLE_TWEEZER_STATISTICS_NAMES[
    "probability_error"
]
TWEEZER_AVERAGE_PROBABILITY_RESULT = _SINGLE_TWEEZER_STATISTICS_NAMES["average"]
TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT = _SINGLE_TWEEZER_STATISTICS_NAMES[
    "average_error"
]
TWEEZER_ROI_NUM_SHOTS_RESULT = _SINGLE_TWEEZER_STATISTICS_NAMES["num_shots"]
TWEEZER_ROI_NUM_BRIGHT_RESULT = _SINGLE_TWEEZER_STATISTICS_NAMES["num_bright"]


def make_statistics_channels(
    make_channel,
    slots,
    conditional_probabilities=(),
):
    """Declare standard slot statistics and any named conditional probabilities."""
    channels = {}
    for slot in slots:
        names = _statistics_result_names(slot)
        shape = slot.result_shape
        probability = make_channel(
            names["probability"],
            ArrayChannel,
            f"{slot.label} per-ROI bright probability",
            element_type="float",
            shape=shape,
            dim_names=ROI_RESULT_DIM_NAMES,
            min=0.0,
            max=1.0,
        )
        average = make_channel(
            names["average"],
            FloatChannel,
            f"{slot.label} mean bright probability across ROIs",
            min=0.0,
            max=1.0,
        )
        channels.update(
            {
                names["probability"]: probability,
                names["probability_error"]: make_channel(
                    names["probability_error"],
                    ArrayChannel,
                    f"{slot.label} per-ROI bright probability error",
                    element_type="float",
                    shape=shape,
                    dim_names=ROI_RESULT_DIM_NAMES,
                    min=0.0,
                    display_hints={"error_bar_for": probability.path},
                ),
                names["average"]: average,
                names["average_error"]: make_channel(
                    names["average_error"],
                    FloatChannel,
                    f"{slot.label} mean bright probability error",
                    min=0.0,
                    display_hints={"error_bar_for": average.path},
                ),
                names["num_shots"]: make_channel(
                    names["num_shots"],
                    ArrayChannel,
                    f"Number of shots contributing to each {slot.label} ROI",
                    element_type="int",
                    shape=shape,
                    dim_names=ROI_RESULT_DIM_NAMES,
                    min=0,
                ),
                names["num_bright"]: make_channel(
                    names["num_bright"],
                    ArrayChannel,
                    f"Number of bright shots in each {slot.label} ROI",
                    element_type="int",
                    shape=shape,
                    dim_names=ROI_RESULT_DIM_NAMES,
                    min=0,
                ),
            }
        )

    if conditional_probabilities:
        # The condition evaluator returns one value for each logical ROI group.
        # All images in a conditional statistic therefore need the same group count,
        # although each image may contain a different number of ROIs per group.
        num_groups = slots[0].result_shape[0]
        for spec in conditional_probabilities:
            names = _conditional_probability_result_names(spec)
            label = spec.label
            probability_by_group = make_channel(
                names["probability_by_group"],
                ArrayChannel,
                f"{label} conditional probability for each ROI group",
                element_type="float",
                shape=(num_groups,),
                dim_names=("group",),
                min=0.0,
                max=1.0,
            )
            pooled_probability = make_channel(
                names["probability"],
                FloatChannel,
                f"{label} conditional probability pooled across ROI groups",
                min=0.0,
                max=1.0,
            )
            condition_channels = {
                names["probability_by_group"]: probability_by_group,
                names["probability_error_by_group"]: make_channel(
                    names["probability_error_by_group"],
                    ArrayChannel,
                    f"{label} conditional-probability error for each ROI group",
                    element_type="float",
                    shape=(num_groups,),
                    dim_names=("group",),
                    min=0.0,
                    display_hints={"error_bar_for": probability_by_group.path},
                ),
                names["num_selected_by_group"]: make_channel(
                    names["num_selected_by_group"],
                    ArrayChannel,
                    f"Shots where the {label.lower()} given-condition was true",
                    element_type="int",
                    shape=(num_groups,),
                    dim_names=("group",),
                    min=0,
                ),
                names["num_successes_by_group"]: make_channel(
                    names["num_successes_by_group"],
                    ArrayChannel,
                    f"Selected shots where the {label.lower()} event was also true",
                    element_type="int",
                    shape=(num_groups,),
                    dim_names=("group",),
                    min=0,
                ),
                names["probability"]: pooled_probability,
                names["probability_error"]: make_channel(
                    names["probability_error"],
                    FloatChannel,
                    f"{label} pooled conditional-probability error",
                    min=0.0,
                    display_hints={"error_bar_for": pooled_probability.path},
                ),
                names["num_selected"]: make_channel(
                    names["num_selected"],
                    IntChannel,
                    f"{label} selected shots pooled across ROI groups",
                    min=0,
                ),
                names["num_successes"]: make_channel(
                    names["num_successes"],
                    IntChannel,
                    f"{label} successful shots pooled across ROI groups",
                    min=0,
                ),
            }
            duplicate_names = channels.keys() & condition_channels.keys()
            if duplicate_names:
                raise ValueError(
                    "Conditional-probability result names collide: "
                    + ", ".join(sorted(duplicate_names))
                )
            channels.update(condition_channels)
    return channels


class AtomImageReadout(Fragment):
    """Acquire, analyse, and publish one or more named atom images.

    "Readout" covers the complete path from a triggered camera exposure to useful
    experiment data: acquiring the frame, publishing the live-view datasets, summing
    the ROIs, applying their thresholds, and generating standard occupation
    statistics.

    The physical cooling and exposure sequence remains the responsibility of the
    species-specific imaging stage; this fragment handles what happens to its image.
    """

    def build_fragment(self, slots, conditional_probabilities=()):
        self.slots = tuple(slots)
        if not self.slots:
            raise ValueError("AtomImageReadout requires at least one image slot")
        prefixes = [slot.result_prefix for slot in self.slots]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("Image-slot result prefixes must be unique")

        self.conditional_probabilities = tuple(conditional_probabilities)
        condition_prefixes = [
            spec.result_prefix for spec in self.conditional_probabilities
        ]
        if len(condition_prefixes) != len(set(condition_prefixes)):
            raise ValueError(
                "Conditional-probability result prefixes must be unique"
            )

        if self.conditional_probabilities:
            group_counts = {slot.result_shape[0] for slot in self.slots}
            if len(group_counts) != 1:
                raise ValueError(
                    "Conditional probabilities require every image slot to have "
                    "the same number of ROI groups"
                )

        self._parsed_conditional_probabilities = tuple(
            (
                spec,
                parse_condition_syntax(spec.event),
                parse_condition_syntax(spec.given),
            )
            for spec in self.conditional_probabilities
        )
        self._validate_conditional_probabilities()

        self._live_imaging_initialised = False
        self._camera_acquiring = False
        self.setattr_device("andor_ctrl")
        self.camera_timeout = self.setattr_param(
            "camera_timeout",
            FloatParam,
            "How long to wait for each camera image before erroring",
            CAMERA_TIMEOUT,
            min=1.0 * ms,
            max=60.0 * s,
        )
        self.camera_timeout: FloatParamHandle

        self._slot_result_channels = tuple(
            self._make_image_result_channels(slot) for slot in self.slots
        )

    def _make_image_result_channels(self, slot):
        shape = slot.result_shape
        return _ImageResultChannels(
            image=self.setattr_result(
                slot.result_name("image"),
                ArrayChannel,
                f"{slot.label} image",
                element_type="int",
                shape=CAMERA_IMAGE_SHAPE,
                dim_names=("y", "x"),
                min=0,
                max=65535,
            ),
            counts=self.setattr_result(
                slot.result_name("roi_counts"),
                ArrayChannel,
                f"Integrated counts in each {slot.label} ROI",
                element_type="int",
                shape=shape,
                dim_names=ROI_RESULT_DIM_NAMES,
                min=0,
            ),
            bright=self.setattr_result(
                slot.result_name("roi_bright"),
                ArrayChannel,
                f"Thresholded bright decision for each {slot.label} ROI",
                element_type="int",
                shape=shape,
                dim_names=ROI_RESULT_DIM_NAMES,
                min=0,
                max=1,
            ),
            thresholds=self.setattr_result(
                slot.result_name("roi_thresholds_applied"),
                ArrayChannel,
                f"Thresholds applied to each {slot.label} ROI",
                element_type="float",
                shape=shape,
                dim_names=ROI_RESULT_DIM_NAMES,
                min=0.0,
            ),
        )

    def make_statistics_channels(self, make_channel):
        """Declare saved copies of every statistic for a repeat wrapper."""
        return make_statistics_channels(
            make_channel,
            self.slots,
            self.conditional_probabilities,
        )

    def _validate_conditional_probabilities(self):
        """Check image/ROI indices at build time using one dummy shot."""

        if not self._parsed_conditional_probabilities:
            return
        dummy_occupancy = tuple(
            np.zeros((1,) + slot.result_shape, dtype=bool) for slot in self.slots
        )
        for spec, event, given in self._parsed_conditional_probabilities:
            try:
                conditional_binomial(
                    dummy_occupancy,
                    event=event,
                    given=given,
                )
            except (IndexError, TypeError, ValueError) as error:
                raise ValueError(
                    f"Invalid conditional probability {spec.result_prefix!r}: "
                    f"event={spec.event!r}, given={spec.given!r}"
                ) from error

    def get_default_analyses(self):
        def make_analysis_channel(name, channel_class, *args, **kwargs):
            return channel_class(name, *args, **kwargs)

        channels = self.make_statistics_channels(make_analysis_channel)
        return [
            CustomAnalysis(
                [],
                self._analyse_statistics,
                analysis_results=list(channels.values()),
                online_fn=self._analyse_statistics,
                online_analysis_identifier="atom_image_statistics",
            )
        ]

    def _analyse_statistics(self, axis_values, result_values, analysis_results):
        del axis_values, analysis_results
        outputs = {}
        occupancy_by_image = []
        for slot, result_channels in zip(self.slots, self._slot_result_channels):
            bright = np.asarray(
                result_values[result_channels.bright],
                dtype=np.int32,
            )
            if bright.size == 0:
                bright = np.empty((0,) + slot.result_shape, dtype=np.int32)
                num_bright = np.zeros(slot.result_shape, dtype=np.int32)
                num_shots = 0
            else:
                bright = bright.reshape((-1,) + slot.result_shape)
                num_bright = np.sum(bright, axis=0, dtype=np.int32)
                num_shots = bright.shape[0]
            occupancy_by_image.append(np.asarray(bright, dtype=bool))

            probability, probability_error = estimate_probability_array(
                num_bright,
                num_shots,
            )
            average_probability = float(np.mean(probability))
            average_probability_error = float(
                np.sqrt(np.sum(probability_error**2)) / probability_error.size
            )
            names = _statistics_result_names(slot)
            outputs.update(
                {
                    names["probability"]: probability,
                    names["probability_error"]: probability_error,
                    names["average"]: average_probability,
                    names["average_error"]: average_probability_error,
                    names["num_shots"]: np.full(
                        slot.result_shape,
                        num_shots,
                        dtype=np.int32,
                    ),
                    names["num_bright"]: num_bright,
                }
            )

        for spec, event, given in self._parsed_conditional_probabilities:
            result = conditional_binomial(
                tuple(occupancy_by_image),
                event=event,
                given=given,
            )
            names = _conditional_probability_result_names(spec)
            outputs.update(
                {
                    names["probability_by_group"]: result.probability_by_group,
                    names["probability_error_by_group"]: (
                        result.probability_error_by_group
                    ),
                    names["num_selected_by_group"]: (
                        result.num_selected_by_group.astype(np.int32)
                    ),
                    names["num_successes_by_group"]: (
                        result.num_successes_by_group.astype(np.int32)
                    ),
                    names["probability"]: result.pooled_probability,
                    names["probability_error"]: result.pooled_probability_error,
                    names["num_selected"]: result.pooled_num_selected,
                    names["num_successes"]: result.pooled_num_successes,
                }
            )
        return AnalysisFeedback(outputs=outputs)

    def host_setup(self):
        super().host_setup()
        self._prepare_continuous_acquisition()
        self._initialise_live_datasets()

    def host_cleanup(self):
        self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self._camera_acquiring = False
        self.andor_ctrl.disable_em_gain()
        super().host_cleanup()

    def _prepare_continuous_acquisition(self):
        """Configure and allocate the circular buffer, but do not arm it yet.

        ``host_setup()`` runs before the RTIO-side lifecycle has established the safe
        state.  In particular, the camera exposure TTL might initially be high after a
        previous failed experiment.  Starting here could therefore create a spurious
        first frame.  ``begin_shot()`` starts the camera after ``device_setup()`` has
        driven that TTL low, and it remains acquiring until ``host_cleanup()``.
        """
        self._camera_acquiring = False
        self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self.andor_ctrl.cooler_on()
        self.andor_ctrl.set_cooler_mode(True)
        self.andor_ctrl.set_temperature(CAMERA_TEMP_C)
        self.andor_ctrl.set_em_gain(CAMERA_GAIN)
        self.andor_ctrl.set_readout_profile(
            output_amplifier=CAMERA_OUTPUT_AMPLIFIER,
            ad_channel=CAMERA_AD_CHANNEL,
            hsspeed_index=CAMERA_HSSPEED_INDEX,
            vsspeed_index=CAMERA_VSSPEED_INDEX,
            preamp_gain_index=CAMERA_PREAMP_GAIN_INDEX,
        )
        self.andor_ctrl.configure_external_exposure_run_till_abort(
            roi=CAMERA_ROI,
            fast_ext_trigger=CAMERA_FAST_EXT_TRIGGER,
            exposure_time_s=CAMERA_EXPOSURE_TIME,
        )
        # PrepareAcquisition allocates the SDK buffer.  Doing this once here removes
        # its comparatively expensive setup work from the boundary between shots.
        self.andor_ctrl.prepare()

    @staticmethod
    def _live_prefix(slot_index):
        return f"live.imaging.slot{slot_index}"

    def _initialise_live_datasets(self):
        # A scheduler pause re-enters host_setup() on the same fragment.  Do not erase
        # ROI edits made in the live viewer during that run.
        if self._live_imaging_initialised:
            return

        for slot_index, slot in enumerate(self.slots):
            prefix = self._live_prefix(slot_index)
            self.set_dataset(
                f"{prefix}.valid",
                False,
                broadcast=True,
                persist=False,
                archive=False,
            )
            self.set_dataset(
                f"{prefix}.rois",
                np.asarray(slot.rois, dtype=np.int32),
                broadcast=True,
                persist=False,
                archive=False,
            )
            self.set_dataset(
                f"{prefix}.thresholds",
                np.asarray(slot.thresholds, dtype=np.float64),
                broadcast=True,
                persist=False,
                archive=False,
            )
        self.set_dataset(
            LIVE_EXPECTED_IMAGES_DATASET,
            len(self.slots),
            broadcast=True,
            persist=False,
            archive=False,
        )
        self._live_imaging_initialised = True

    def _set_slot_valid(self, slot_index, valid):
        self.set_dataset(
            f"{self._live_prefix(slot_index)}.valid",
            bool(valid),
            broadcast=True,
            persist=False,
            archive=False,
        )

    @rpc
    def begin_shot(self):
        """Invalidate every slot and ensure the continuous acquisition is running."""
        for slot_index in range(len(self.slots)):
            self._set_slot_valid(slot_index, False)

        # The first begin_shot() follows LabLifecycle.device_setup(), so the exposure
        # TTL is known to be low.  Later shots reuse this acquisition and only drain
        # their frames from the SDK circular buffer.
        if not self._camera_acquiring:
            self.andor_ctrl.start_acquisition()
            self._camera_acquiring = True

    def _wait_get_images(self, n_images):
        return self.andor_ctrl.wait_get_images16(
            n_images,
            timeout_ms=int(1000.0 * self.camera_timeout.get()),
        )

    def _read_live_rois_and_thresholds(self, slot_index):
        slot = self.slots[slot_index]
        prefix = self._live_prefix(slot_index)
        rois = np.asarray(
            self.get_dataset(f"{prefix}.rois", archive=False),
            dtype=np.int32,
        )
        thresholds = np.asarray(
            self.get_dataset(f"{prefix}.thresholds", archive=False),
            dtype=np.float64,
        )
        # Accept a flat threshold row for the common one-group case; this is convenient
        # when editing datasets manually and preserves the previous behaviour.
        if thresholds.shape == (slot.result_shape[-1],):
            thresholds = thresholds.reshape(slot.result_shape)
        if rois.shape != slot.result_shape + (4,):
            raise ValueError(
                f"{prefix}.rois has shape {rois.shape}; expected "
                f"{slot.result_shape + (4,)}"
            )
        if thresholds.shape != slot.result_shape:
            raise ValueError(
                f"{prefix}.thresholds has shape {thresholds.shape}; expected "
                f"{slot.result_shape}"
            )
        return rois, thresholds

    def _process_and_publish(self, slot_index, image):
        prefix = self._live_prefix(slot_index)
        rois, thresholds = self._read_live_rois_and_thresholds(slot_index)
        roi_counts = sum_counts_in_rois(image, rois, dtype=np.int64)
        roi_bright = threshold_counts_to_occupancy(roi_counts, thresholds).astype(
            np.int32
        )

        channels = self._slot_result_channels[slot_index]
        channels.image.push(image)
        channels.counts.push(roi_counts)
        channels.bright.push(roi_bright)
        channels.thresholds.push(thresholds)

        # Image first, valid last: ``valid`` is the slot's commit marker.
        self.set_dataset(
            f"{prefix}.image",
            image,
            broadcast=True,
            persist=False,
            archive=False,
        )
        self._set_slot_valid(slot_index, True)

    @rpc
    def wait_read_all(self):
        """Drain and process this shot's images in declared slot order.

        All exposure TTL events can therefore occur in one uninterrupted RTIO phase.
        The host only waits for and analyses the images after that phase is complete.
        """
        images = self._wait_get_images(len(self.slots))
        for slot_index, image in enumerate(images):
            self._process_and_publish(slot_index, image)
