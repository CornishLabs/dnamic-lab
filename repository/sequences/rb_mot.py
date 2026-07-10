import numpy as np

from artiq.coredevice.ad9910 import AD9910
from artiq.coredevice.core import Core
from artiq.coredevice.suservo import Channel as SUServoChannel
from artiq.coredevice.suservo import SUServo
from artiq.coredevice.ttl import TTLOut
from artiq.coredevice.urukul import CPLD
from artiq.coredevice.zotino import Zotino

from artiq.experiment import kernel
from artiq.experiment import rpc

from artiq.language.core import delay

from artiq.language.units import MHz
from artiq.language.units import V
from artiq.language.units import ms
from artiq.language.units import s

from ndscan.define.fragment import ExpFragment
from ndscan.define.fragment import Fragment
from ndscan.define.default_analysis import AnalysisFeedback
from ndscan.define.default_analysis import CustomAnalysis
from ndscan.define.parameters import FloatParam
from ndscan.define.parameters import FloatParamHandle
from ndscan.define.result_channels import ArrayChannel

from ndscan.runtime.api import make_fragment_prepared_dashboard_scan_exp


DDS_ATTEN_DB = 8.0 # This is what we use across all DDS Channels
TWEEZER_SUSERVO_FREQ = 80.0 * MHz
TWEEZER_SUSERVO_ATTEN_DB = 8.0
TWEEZER_SUSERVO_ADC_CHANNEL = 0
TWEEZER_SUSERVO_PGIA_GAIN = 0
TWEEZER_SUSERVO_TARGET_V = 1.15
TWEEZER_SUSERVO_OFFSET = -TWEEZER_SUSERVO_TARGET_V * (
    10.0 ** (TWEEZER_SUSERVO_PGIA_GAIN - 1)
)
TWEEZER_SUSERVO_KP = -1.8
TWEEZER_SUSERVO_KI = -1_550_000.0
TWEEZER_SUSERVO_GAIN_LIMIT = 0.0

CAMERA_TEMP_C = -60
CAMERA_GAIN = 100
CAMERA_OUTPUT_AMPLIFIER = 0
CAMERA_AD_CHANNEL = 0
CAMERA_HSSPEED_INDEX = 2
CAMERA_VSSPEED_INDEX = 4
CAMERA_PREAMP_GAIN_INDEX = 2  # Old preampgain=3 was a 1-based UI setting.
CAMERA_EXPOSURE_TIME = 30.0 * ms
CAMERA_TIMEOUT = 20.0 * s
CAMERA_FAST_EXT_TRIGGER = True
CAMERA_ROI = (0, 511, 0, 511)  # x0, x1, y0, y1 (inclusive)

SHUTTER_PREFIRE = 10.0 * ms
MOT_HOLD_TIME = 1.0 * s
MOLASSES_TIME = 30.0 * ms
COOLING_TIME = 10.0 * ms

NUM_TWEEZER_ROIS = 5
NUM_TWEEZER_ROI_GROUPS = 1
TWEEZER_ROI_RESULT_SHAPE = (NUM_TWEEZER_ROI_GROUPS, NUM_TWEEZER_ROIS)
TWEEZER_ROI_RESULT_DIM_NAMES = ("group", "roi")
TWEEZER_ROIS_DATASET = "rb_mot.tweezer_rois"
TWEEZER_ROI_THRESHOLDS_DATASET = "rb_mot.tweezer_roi_thresholds"
TWEEZER_ROI_COUNTS_DATASET = "rb_mot.tweezer_roi_counts"
TWEEZER_ROI_BRIGHT_DATASET = "rb_mot.tweezer_roi_bright"
TWEEZER_ROI_PROBABILITY_RESULT = "tweezer_roi_bright_probability"
TWEEZER_ROI_PROBABILITY_ERROR_RESULT = "tweezer_roi_bright_probability_error"
TWEEZER_ROI_NUM_SHOTS_RESULT = "tweezer_roi_num_shots"
TWEEZER_ROI_NUM_BRIGHT_RESULT = "tweezer_roi_num_bright"

# These ROI bounds are Python-style half-open bounds: (y0, y1, x0, x1).
# The extra outer list is the ROI "group" dimension expected by image_roi_applet.py.
DEFAULT_TWEEZER_ROIS = (
    (
        (255, 258, 127, 130),
        (242, 245, 127, 130),
        (227, 230, 127, 130),
        (213, 216, 127, 130),
        (199, 202, 127, 130),
    ),
)

# Integrated-count thresholds for the five ROIs. This deliberately starts
# uncalibrated; set rb_mot.tweezer_roi_thresholds from real empty/loaded shots.
DEFAULT_TWEEZER_ROI_THRESHOLDS = ((1.0e12, 1.0e12, 1.0e12, 1.0e12, 1.0e12),)


def _get_tweezer_rois(owner):
    try:
        rois = owner.get_dataset(TWEEZER_ROIS_DATASET)
    except KeyError:
        rois = DEFAULT_TWEEZER_ROIS
        owner.set_dataset(
            TWEEZER_ROIS_DATASET,
            rois,
            broadcast=True,
            persist=True,
        )
    return np.asarray(rois, dtype=np.int32)


def _get_tweezer_roi_thresholds(owner):
    try:
        thresholds = owner.get_dataset(TWEEZER_ROI_THRESHOLDS_DATASET)
    except KeyError:
        thresholds = DEFAULT_TWEEZER_ROI_THRESHOLDS
        owner.set_dataset(
            TWEEZER_ROI_THRESHOLDS_DATASET,
            thresholds,
            broadcast=True,
            persist=True,
        )
    thresholds = np.asarray(thresholds, dtype=np.float64)
    if thresholds.shape == (NUM_TWEEZER_ROIS,):
        thresholds = thresholds.reshape(TWEEZER_ROI_RESULT_SHAPE)
    return thresholds


def _ensure_tweezer_roi_datasets(owner):
    owner.set_dataset(
        TWEEZER_ROIS_DATASET,
        _get_tweezer_rois(owner).tolist(),
        broadcast=True,
        persist=True,
    )
    owner.set_dataset(
        TWEEZER_ROI_THRESHOLDS_DATASET,
        _get_tweezer_roi_thresholds(owner).tolist(),
        broadcast=True,
        persist=True,
    )


def _sum_tweezer_rois(image, rois):
    image = np.asarray(image)
    return np.asarray(
        [
            [
                np.sum(image[y0:y1, x0:x1], dtype=np.int64)
                for y0, y1, x0, x1 in roi_group
            ]
            for roi_group in rois
        ],
        dtype=np.int64,
    )


def _estimate_binomial_probability(num_successes, num_shots):
    if num_shots <= 0:
        shape = np.shape(num_successes)
        return np.zeros(shape, dtype=np.float64), np.full(shape, np.inf, dtype=np.float64)
    probability = np.asarray(num_successes, dtype=np.float64) / float(num_shots)
    error = np.sqrt(np.maximum(probability * (1.0 - probability), 0.0) / float(num_shots))
    error = np.maximum(error, 0.5 / float(num_shots))
    return probability, error


def configure_andor_for_rb_single_image(andor_ctrl):
    """Apply the old Andor single-image profile used for Rb images."""
    andor_ctrl.abort_acquisition(ignore_idle=True)
    andor_ctrl.cooler_on()
    andor_ctrl.set_cooler_mode(True)
    andor_ctrl.set_temperature(CAMERA_TEMP_C)
    andor_ctrl.set_em_gain(CAMERA_GAIN)
    andor_ctrl.set_readout_profile(
        output_amplifier=CAMERA_OUTPUT_AMPLIFIER,
        ad_channel=CAMERA_AD_CHANNEL,
        hsspeed_index=CAMERA_HSSPEED_INDEX,
        vsspeed_index=CAMERA_VSSPEED_INDEX,
        preamp_gain_index=CAMERA_PREAMP_GAIN_INDEX,
    )
    andor_ctrl.configure_external_exposure_run_till_abort(
        roi=CAMERA_ROI,
        fast_ext_trigger=CAMERA_FAST_EXT_TRIGGER,
        exposure_time_s=CAMERA_EXPOSURE_TIME,
    )

# MOT stage good values
RB_COOL_DDS_FREQ_MHZ_MOT = 101.25
RB_COOL_DDS_ASF_MOT = 0.48
RB_REPUMP_DDS_FREQ_MHZ_MOT = 80.64
RB_REPUMP_DDS_ASF_MOT = 0.32
RB_EW_SHIMS_V_MOT = -0.367 # -0.406
RB_UD_SHIMS_V_MOT = 0.8    #  0.867
RB_NS_SHIMS_V_MOT = -0.112 # -0.122
RB_QUAD_V_MOT = 8.8

# Initial transfer/imaging values from the old control system. ASFs are scaled
# from the MOT dBm values, assuming DDS ASF is an RF voltage amplitude.
RB_COOL_DDS_FREQ_MHZ_MOLASSES = 136.16 # CHECKED
RB_COOL_DDS_ASF_MOLASSES = 0.67
RB_REPUMP_DDS_ASF_MOLASSES = 0.28
RB_EW_SHIMS_V_MOLASSES = -0.12
RB_UD_SHIMS_V_MOLASSES = 1.15
RB_NS_SHIMS_V_MOLASSES = 0.55


# COOLING STAGE BEFORE IMAGING
RB_COOL_DDS_FREQ_MHZ_TWEEZER_COOLING = 125.54 # CHECKED
RB_COOL_DDS_ASF_TWEEZER_COOLING = 0.43
RB_REPUMP_DDS_ASF_TWEEZER_COOLING= 0.2

# IMAGING
RB_COOL_DDS_FREQ_MHZ_TWEEZER_IMAGE = 103.49 # CHECKED
RB_COOL_DDS_ASF_TWEEZER_IMAGE = 0.415
RB_REPUMP_DDS_ASF_TWEEZER_IMAGE = 0.12

RB_EW_SHIMS_V_IMAGING_COOLING = -0.05  #CHECKED
RB_UD_SHIMS_V_IMAGING_COOLING = 1.1
RB_NS_SHIMS_V_IMAGING_COOLING = 0.1

class HardwareInitOnce(Fragment):
    # TODO: Eventually this fragment should not manually enumerate the names of the
    #       hardware, instead it should lookup the device db names.

    def build_fragment(self):
        self.setattr_device("core")
        self.core: Core

        # DDSs
        self.setattr_device("dds_cpld_rb")
        self.dds_cpld_rb: CPLD
        self.setattr_device("dds_ch_rb_cool")
        self.dds_ch_rb_cool: AD9910
        self.setattr_device("dds_ch_rb_repump")
        self.dds_ch_rb_repump: AD9910

        # DAC
        self.setattr_device("zotino0")
        self.zotino0: Zotino

        # SUServo
        self.setattr_device("suservo0")
        self.suservo0: SUServo
        self.setattr_device("suservo0_ch0")
        self.suservo0_ch0: SUServoChannel

        # Local variables
        self._needs_hardware_init = True
    
    def host_setup(self):
        super().host_setup()

        self.suservo_profile = self.suservo0_ch0.servo_channel
        self.suservo_attenuator_channel = self.suservo0_ch0.servo_channel % 4
        self.suservo_cpld = self.suservo0_ch0.dds.cpld

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {
            "suservo0",
            "suservo0_ch0",
            "suservo_profile",
            "suservo_attenuator_channel",
            "suservo_cpld",
        }

        # ndscan calls host_setup after a scheduler pause is eventually resumed.
        # This will invalidate the init state and cause a reinitialisation.
        # This is probably unnecessary as it is unlikely a seperate experiment will
        # invalidate the 'initialisation' of the device, but is safe.
        self._needs_hardware_init = True

    # We could choose to do this in host_cleanup too if we want.

    @kernel
    def device_setup(self):
        if not self._needs_hardware_init:
            return
        self._needs_hardware_init = False

        # Initialise all the devices

        # Core
        self.core.reset() # (also does break realtime)
        delay(10.0 * ms)

        # DDSs
        self.dds_cpld_rb.init()
        # I am not entirely sure why, but these DDS initialisations need more
        # slack.
        ## Maybe the channel initialisations wait an indeterminate amount of time 
        #    due to a PLL lock check, so break_realtime afterwords to stop
        #    pseudorandom RTIOUnderflow.
        self.core.break_realtime()
        delay(40*ms)  
        self.dds_ch_rb_cool.init()
        self.core.break_realtime()
        delay(40*ms)     
        self.dds_ch_rb_repump.init()
        self.core.break_realtime()

        # DAC
        self.zotino0.init()

        # SUServos
        self.core.break_realtime()
        self.suservo0.init()
        self.core.break_realtime()
        delay(1.0 * ms)

        self.suservo0.set_config(enable=0)
        self.suservo0.set_pgia_mu(
            TWEEZER_SUSERVO_ADC_CHANNEL,
            TWEEZER_SUSERVO_PGIA_GAIN,
        )
        self.suservo_cpld.set_att(
            self.suservo_attenuator_channel,
            TWEEZER_SUSERVO_ATTEN_DB,
        )
        self.suservo0_ch0.set_iir(
            profile=self.suservo_profile,
            adc=TWEEZER_SUSERVO_ADC_CHANNEL,
            kp=TWEEZER_SUSERVO_KP,
            ki=TWEEZER_SUSERVO_KI,
            g=TWEEZER_SUSERVO_GAIN_LIMIT,
        )
        self.suservo0_ch0.set_dds(
            profile=self.suservo_profile,
            frequency=TWEEZER_SUSERVO_FREQ,
            offset=TWEEZER_SUSERVO_OFFSET,
        )
        self.suservo0_ch0.set_y( # Set integrator (output) to zero
            profile=self.suservo_profile,
            y=0.0,
        )
        self.suservo0_ch0.set(
            en_out=0, # RF switch off
            en_iir=0, # IIR integrator updates off (unservoed)
            profile=self.suservo_profile,
        )

        self.suservo0.set_config(enable=1) # Enable SUServo write cycle
        self.core.break_realtime()


class SafeHardwareState(Fragment):
    """
    Put the MOT hardware owned by this file into a safe/off state.

    This intentionally does not initialise hardware; it only turns outputs off
    and zeros the field DAC channels.
    """

    def build_fragment(self):
        self.setattr_device("core")
        self.core: Core

        self.setattr_device("ttl_camera_exposure")
        self.ttl_camera_exposure: TTLOut
        self.setattr_device("ttl_quad")
        self.ttl_quad: TTLOut
        self.setattr_device("ttl_rb_cool_shut")
        self.ttl_rb_cool_shut: TTLOut
        self.setattr_device("ttl_rb_repump_shut")
        self.ttl_rb_repump_shut: TTLOut

        self.setattr_device("dds_ch_rb_cool")
        self.dds_ch_rb_cool: AD9910
        self.setattr_device("dds_ch_rb_repump")
        self.dds_ch_rb_repump: AD9910

        self.setattr_device("zotino0")
        self.zotino0: Zotino

        # SUServo
        self.setattr_device("suservo0")
        self.suservo0: SUServo
        self.setattr_device("suservo0_ch0")
        self.suservo0_ch0: SUServoChannel

    def host_setup(self):
        super().host_setup()

        self.suservo_profile = self.suservo0_ch0.servo_channel
        self.suservo_attenuator_channel = self.suservo0_ch0.servo_channel % 4
        self.suservo_cpld = self.suservo0_ch0.dds.cpld

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {
            "suservo0",
            "suservo0_ch0",
            "suservo_profile",
            "suservo_attenuator_channel",
            "suservo_cpld",
        }


    @kernel
    def set_safe(self):
        self.core.break_realtime()

        self.ttl_camera_exposure.off()
        self.ttl_quad.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()
        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()

        self.zotino0.set_dac(
            [0.0 * V, 0.0 * V, 0.0 * V, 0.0 * V],
            [0, 1, 2, 3],
        )

        delay(1*ms)
        self.dds_ch_rb_cool.set_att(DDS_ATTEN_DB)
        self.dds_ch_rb_repump.set_att(DDS_ATTEN_DB)

        self.suservo0.set_config(enable=0)
        self.suservo0_ch0.set_y( # Set integrator (output) to zero
            profile=self.suservo_profile,
            y=0.0,
        )
        self.suservo0_ch0.set(
            en_out=0, # RF switch off
            en_iir=0, # IIR integrator updates off (unservoed)
            profile=self.suservo_profile,
        )
        self.suservo0.set_config(enable=1) # Enable SUServo write cycle

    @kernel
    def device_setup(self):
        self.set_safe()

    @kernel
    def device_cleanup(self):
        self.set_safe()

class KnownHardwareState(Fragment):
    """Initialise hardware when needed, then put it into a safe state."""

    def build_fragment(self):
        self.setattr_fragment("hardware_init", HardwareInitOnce)
        self.hardware_init: HardwareInitOnce

        self.setattr_fragment("safe_state", SafeHardwareState)
        self.safe_state: SafeHardwareState

    # NOTE: The default ndscan implementations are enough here:
    # device_setup() runs subfragments in declaration order, so hardware is
    # initialised first and then put into a safe state. device_cleanup() runs
    # subfragments in reverse order, so this fragment also provides the final
    # safe teardown when the top-level experiment exits, pauses, or fails.
    #
    # Add KnownHardwareState as the first subfragment of an ExpFragment so later
    # stage fragments can apply their per-point settings after the safe state.


class TweezerSUServoToneService(Fragment):
    """Servoed SU-Servo channel 0 output used as the tweezer RF drive."""

    def build_fragment(self):
        self.setattr_device("core")
        self.core: Core

        self.setattr_device("suservo0")
        self.suservo0: SUServo
        self.setattr_device("suservo0_ch0")
        self.suservo0_ch0: SUServoChannel

    def host_setup(self):
        super().host_setup()

        self.suservo_profile = self.suservo0_ch0.servo_channel
        self.suservo_attenuator_channel = self.suservo0_ch0.servo_channel % 4
        self.suservo_cpld = self.suservo0_ch0.dds.cpld

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {
            "suservo0",
            "suservo0_ch0",
            "suservo_profile",
            "suservo_attenuator_channel",
            "suservo_cpld",
        }

    @kernel
    def turn_on(self):
        self.suservo0_ch0.set(
            en_out=0,
            en_iir=0,
            profile=self.suservo_profile,
        )
        self.suservo0_ch0.set_y(
            profile=self.suservo_profile,
            y=0.0,
        )
        self.suservo0_ch0.set(
            en_out=1,
            en_iir=1,
            profile=self.suservo_profile,
        )

    @kernel
    def turn_off(self):
        self.suservo0_ch0.set(
            en_out=0,
            en_iir=0,
            profile=self.suservo_profile,
        )


class RbLightService(Fragment):

    def build_fragment(
        self,
        cool_frequency_default=RB_COOL_DDS_FREQ_MHZ_MOT*MHz,
        repump_frequency_default=RB_REPUMP_DDS_FREQ_MHZ_MOT*MHz,
        cool_dds_amp_default=RB_COOL_DDS_ASF_MOT,
        repump_dds_amp_default=RB_REPUMP_DDS_ASF_MOT,
        shutter_prefire_default=SHUTTER_PREFIRE,
    ):
        self.setattr_param("cool_frequency",
                           FloatParam,
                           "Cool light AOM drive frequency",
                           cool_frequency_default,
                           min=(110-50)*MHz, max=(110+50)*MHz)
        self.cool_frequency: FloatParamHandle

        self.setattr_param("repump_frequency",
                           FloatParam,
                           "Repump light AOM drive frequency",
                           repump_frequency_default,
                           min=(80-50)*MHz, max=(80+50)*MHz)
        self.repump_frequency: FloatParamHandle

        self.setattr_param("cool_dds_amp",
                           FloatParam,
                           "Cool light AOM DDS amp (0-1)",
                           cool_dds_amp_default,
                           min=0, max=1)
        self.cool_dds_amp: FloatParamHandle

        self.setattr_param("repump_dds_amp",
                           FloatParam,
                           "Repump light AOM DDS amp (0-1)",
                           repump_dds_amp_default,
                           min=0, max=1)
        self.repump_dds_amp: FloatParamHandle
        
        self.setattr_param("shutter_prefire",
                           FloatParam,
                           "How much time to allow for the shutter coming on, before turning the light on",
                           shutter_prefire_default,
                           min=0*ms, max=200*ms)
        self.shutter_prefire: FloatParamHandle
        
        self.setattr_device("core")
        self.core: Core
        self.setattr_device("dds_ch_rb_cool")
        self.dds_ch_rb_cool: AD9910
        self.setattr_device("dds_ch_rb_repump")
        self.dds_ch_rb_repump: AD9910
        self.setattr_device("dds_cpld_rb")
        self.dds_cpld_rb: CPLD
        self.setattr_device("ttl_rb_cool_shut")
        self.ttl_rb_cool_shut: TTLOut
        self.setattr_device("ttl_rb_repump_shut")
        self.ttl_rb_repump_shut: TTLOut
    
    # --- In seq action funcs ---

    @kernel
    def apply_dds_settings(self):
        """
        Changes the DDS parameters to a different freq/amp.
        This function can be used alone to just change the beam freq/amp
        without changing the state of the RF switches.
        """
        self.dds_ch_rb_cool.set(self.cool_frequency.use(), amplitude=self.cool_dds_amp.use())
        self.dds_ch_rb_repump.set(self.repump_frequency.use(), amplitude=self.repump_dds_amp.use())

    @kernel
    def device_setup(self):
        self.core.break_realtime()
        # TODO: CHECK
        self.apply_dds_settings()
        self.turn_light_off_now(close_shutters=True)

    @kernel
    def turn_light_on_now(self, program_profile=False, pre_open_shutters=True):
        """
        Turn the cool+repump beams on. This function opens the requisite shutters
        first, waits for the shutter prefire time, and then enables the RF switches.
        """
        if program_profile:
            self.apply_dds_settings()
        if pre_open_shutters:
            shutter_prefire = self.shutter_prefire.get()
            self.ttl_rb_cool_shut.on()
            self.ttl_rb_repump_shut.on()
            delay(shutter_prefire)
        # If the switch was already on, this a Noop
        self.dds_ch_rb_cool.sw.on()
        self.dds_ch_rb_repump.sw.on()
    
    @kernel
    def turn_light_off_now(self, close_shutters=True):
        """
        Turn the cool+repump beams off `now`. This function will close the shutters (if asked)
        and RF switches.
        """
        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        if close_shutters:
            self.ttl_rb_cool_shut.off()
            self.ttl_rb_repump_shut.off()

class LowBFieldService(Fragment):
    """
    Set the shim fields to the given setpoints. This currently uses the DAC to send voltages to
    the control drivers.
    """

    def build_fragment(
        self,
        EW_setpoint_default=RB_EW_SHIMS_V_MOT*V,
        UD_setpoint_default=RB_UD_SHIMS_V_MOT*V,
        NS_setpoint_default=RB_NS_SHIMS_V_MOT*V,
        quad_setpoint_default=RB_QUAD_V_MOT*V,
    ):
        self.setattr_param("EW_setpoint",
                           FloatParam,
                           "E/W Shims servo setpoint voltage",
                           EW_setpoint_default,
                           min=-10*V, max=+10*V)
        self.EW_setpoint: FloatParamHandle

        self.setattr_param("UD_setpoint",
                           FloatParam,
                           "U/D Shims servo setpoint voltage",
                           UD_setpoint_default,
                           min=-10*V, max=+10*V)
        self.UD_setpoint: FloatParamHandle
        
        self.setattr_param("NS_setpoint",
                           FloatParam,
                           "N/S Shims servo setpoint voltage",
                           NS_setpoint_default,
                           min=-10*V, max=+10*V)
        self.NS_setpoint: FloatParamHandle

        self.setattr_param("quad_setpoint",
                    FloatParam,
                    "Quad setpoint",
                    quad_setpoint_default,
                    min=0*V,max=10*V
                    )
        self.quad_setpoint:FloatParamHandle
        
        self.setattr_device("core")
        self.core: Core
        self.setattr_device("ttl_quad")
        self.ttl_quad: TTLOut
        self.setattr_device("zotino0")
        self.zotino0: Zotino

    # -- In Seq action functions --
    @kernel
    def set_setpoints(self):
        self.zotino0.set_dac(
            [self.EW_setpoint.use(), self.UD_setpoint.use(), self.NS_setpoint.use(), self.quad_setpoint.use()],
            [0, 1, 2, 3],
        )

    @kernel
    def turn_quad_on(self):
        self.ttl_quad.on()
    
    @kernel
    def turn_quad_off(self):
        self.ttl_quad.off()


class RbMOTLoadService(Fragment):
    def build_fragment(self):
        self.setattr_fragment("MOT_load_fields", LowBFieldService)
        self.MOT_load_fields: LowBFieldService
        self.setattr_fragment("MOT_fluoresce", RbLightService)
        self.MOT_fluoresce: RbLightService

        self.setattr_device("core")
        self.core: Core
        self.setattr_device("zotino0")
        self.zotino0: Zotino

    @kernel
    def load_mot_on(self):
        self.MOT_fluoresce.apply_dds_settings()
        self.MOT_load_fields.set_setpoints()
        self.MOT_load_fields.turn_quad_on()
        self.MOT_fluoresce.turn_light_on_now(program_profile=False, pre_open_shutters=True)

    @kernel
    def load_mot_off(self):
        self.MOT_load_fields.turn_quad_off()
        self.MOT_fluoresce.turn_light_off_now(close_shutters=True)


class LoadRbMOTImage(ExpFragment):
    def build_fragment(self):
        self.setattr_fragment("known_hardware_state", KnownHardwareState)
        self.known_hardware_state: KnownHardwareState

        self.setattr_fragment("Rb_MOT_loader", RbMOTLoadService)
        self.Rb_MOT_loader: RbMOTLoadService

        self.setattr_param("Rb_MOT_preload_time",
                           FloatParam,
                           "How long to load the MOT for before starting exposing the camera",
                           1.0*s,
                           min=1.0*ms,max=10.0*s)
        self.Rb_MOT_preload_time:FloatParamHandle

        self.setattr_param("exposure_time",
                           FloatParam,
                           "How long to expose the camera for",
                           0.5*s,
                           min=0*s,max=10*s)
        self.exposure_time: FloatParamHandle

        self.setattr_param("camera_timeout",
                           FloatParam,
                           "How long to wait for the camera image before erroring",
                           CAMERA_TIMEOUT,
                           min=1.0*ms,max=60.0*s)
        self.camera_timeout: FloatParamHandle

        # Results
        self.setattr_result(
            "mot_image",
            ArrayChannel,
            element_type="int",
            shape=(512, 512),
            dim_names=("y", "x"),
            min=0,
            max=65535,
        )

        # Devices
        self.setattr_device("core")
        self.core: Core
        self.setattr_device("andor_ctrl")
        self.setattr_device("ttl_camera_exposure")
        self.ttl_camera_exposure: TTLOut

    def _configure_camera(self):
        configure_andor_for_rb_single_image(self.andor_ctrl)

    def host_setup(self):
        super().host_setup()
        self._configure_camera()
        _ensure_tweezer_roi_datasets(self)

    def host_cleanup(self):
        self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self.andor_ctrl.disable_em_gain()
        super().host_cleanup()

    @rpc
    def camera_start_acquisition(self):
        self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self.andor_ctrl.prepare()
        self.andor_ctrl.start_acquisition()

    @rpc
    def camera_wait_read_and_publish(self, timeout_s):
        try:
            img = self.andor_ctrl.wait_get_image16(timeout_ms=int(1000.0 * timeout_s))
        finally:
            self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self.mot_image.push(img)
        self.set_dataset("andor.image", img, broadcast=True)

    @kernel
    def rtio_events(self):
        self.core.break_realtime()
        delay(20.0 * ms)
        
        self.Rb_MOT_loader.load_mot_on()
        delay(self.Rb_MOT_preload_time.get())
        self.ttl_camera_exposure.pulse(self.exposure_time.get())
        self.Rb_MOT_loader.load_mot_off()
        self.known_hardware_state.safe_state.set_safe()
    
    @kernel
    def run_once(self):
        self.camera_start_acquisition()
        self.core.break_realtime()
        self.rtio_events()
        self.camera_wait_read_and_publish(self.camera_timeout.get())


LoadRbMOTImageExp = make_fragment_prepared_dashboard_scan_exp(
    LoadRbMOTImage,
    max_rtio_underflow_retries=0,
)


class RbMolassesService(Fragment):
    def build_fragment(self):
        self.setattr_fragment(
            "molasses_fields",
            LowBFieldService,
            RB_EW_SHIMS_V_MOLASSES*V,
            RB_UD_SHIMS_V_MOLASSES*V,
            RB_NS_SHIMS_V_MOLASSES*V,
            0.0*V,
        )
        self.molasses_fields: LowBFieldService

        self.setattr_fragment(
            "molasses_light",
            RbLightService,
            RB_COOL_DDS_FREQ_MHZ_MOLASSES*MHz,
            RB_REPUMP_DDS_FREQ_MHZ_MOT*MHz,
            RB_COOL_DDS_ASF_MOLASSES,
            RB_REPUMP_DDS_ASF_MOLASSES,
        )
        self.molasses_light: RbLightService

    @kernel
    def molasses_on(self):
        self.molasses_fields.set_setpoints()
        delay(0.5*ms)
        self.molasses_light.apply_dds_settings()
        delay(0.5*ms)
        self.molasses_fields.turn_quad_off()

class RbCoolingService(Fragment):
    def build_fragment(self):
        self.setattr_fragment(
            "cooling_fields",
            LowBFieldService,
            RB_EW_SHIMS_V_IMAGING_COOLING*V,
            RB_UD_SHIMS_V_IMAGING_COOLING*V,
            RB_NS_SHIMS_V_IMAGING_COOLING*V,
            0.0*V,
        )
        self.cooling_fields: LowBFieldService

        self.setattr_fragment(
            "cooling_light",
            RbLightService,
            RB_COOL_DDS_FREQ_MHZ_TWEEZER_COOLING*MHz,
            RB_REPUMP_DDS_FREQ_MHZ_MOT*MHz,
            RB_COOL_DDS_ASF_TWEEZER_COOLING,
            RB_REPUMP_DDS_ASF_TWEEZER_COOLING,
        )
        self.cooling_light: RbLightService

    @kernel
    def cooling_on(self):
        self.cooling_fields.set_setpoints()
        self.cooling_light.apply_dds_settings()

class RbTweezerImageService(Fragment):
    def build_fragment(self):
        self.setattr_fragment(
            "imaging_light",
            RbLightService,
            RB_COOL_DDS_FREQ_MHZ_TWEEZER_IMAGE*MHz,
            RB_REPUMP_DDS_FREQ_MHZ_MOT*MHz,
            RB_COOL_DDS_ASF_TWEEZER_IMAGE,
            RB_REPUMP_DDS_ASF_TWEEZER_IMAGE,
        )
        self.imaging_light: RbLightService

        self.setattr_param("exposure_time",
                           FloatParam,
                           "How long to expose the camera for",
                           CAMERA_EXPOSURE_TIME,
                           min=1.0*ms,max=1.0*s)
        self.exposure_time: FloatParamHandle

        self.setattr_device("ttl_camera_exposure")
        self.ttl_camera_exposure: TTLOut

    @kernel
    def image_atoms(self,exposure_trigger=True):
        self.imaging_light.apply_dds_settings()
        self.ttl_camera_exposure.on() if exposure_trigger else self.ttl_camera_exposure.off()
        delay(self.exposure_time.use())
        self.ttl_camera_exposure.off()
        self.imaging_light.turn_light_off_now(close_shutters=True)


class LoadRbMOTToTweezersImage(ExpFragment):
    def build_fragment(self):
        self.setattr_fragment("known_hardware_state", KnownHardwareState)
        self.known_hardware_state: KnownHardwareState

        self.setattr_fragment("tweezer_tone", TweezerSUServoToneService)
        self.tweezer_tone: TweezerSUServoToneService

        self.setattr_fragment("Rb_MOT_loader", RbMOTLoadService)
        self.Rb_MOT_loader: RbMOTLoadService

        self.setattr_fragment("Rb_molasses", RbMolassesService)
        self.Rb_molasses: RbMolassesService

        self.setattr_fragment("Rb_cooling", RbCoolingService)
        self.Rb_cooling: RbCoolingService

        self.setattr_fragment("tweezer_imager", RbTweezerImageService)
        self.tweezer_imager: RbTweezerImageService

        self.setattr_param("mot_hold_time",
                           FloatParam,
                           "How long to hold the MOT before transfer",
                           MOT_HOLD_TIME,
                           min=1.0*ms,max=10.0*s)
        self.mot_hold_time: FloatParamHandle

        self.setattr_param("molasses_time",
                           FloatParam,
                           "How long to hold molasses settings before imaging",
                           MOLASSES_TIME,
                           min=0.0*ms,max=1.0*s)
        self.molasses_time: FloatParamHandle

        self.setattr_param("cooling_time",
                           FloatParam,
                           "How long to hold cooling settings before imaging",
                           COOLING_TIME,
                           min=0.0*ms,max=1.0*s)
        self.cooling_time: FloatParamHandle

        self.setattr_param("camera_timeout",
                           FloatParam,
                           "How long to wait for the camera image before erroring",
                           CAMERA_TIMEOUT,
                           min=1.0*ms,max=60.0*s)
        self.camera_timeout: FloatParamHandle

        self.setattr_result(
            "tweezers_image",
            ArrayChannel,
            element_type="int",
            shape=(512, 512),
            dim_names=("y", "x"),
            min=0,
            max=65535,
        )
        self.setattr_result(
            "tweezer_roi_counts",
            ArrayChannel,
            "Integrated counts in each tweezer ROI",
            element_type="int",
            shape=TWEEZER_ROI_RESULT_SHAPE,
            dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
            min=0,
        )
        self.setattr_result(
            "tweezer_roi_bright",
            ArrayChannel,
            "Thresholded bright/loaded decision for each tweezer ROI",
            element_type="int",
            shape=TWEEZER_ROI_RESULT_SHAPE,
            dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
            min=0,
            max=1,
        )
        self.setattr_result(
            "tweezer_roi_thresholds_applied",
            ArrayChannel,
            "Integrated-count thresholds applied to each tweezer ROI",
            element_type="float",
            shape=TWEEZER_ROI_RESULT_SHAPE,
            dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
            min=0.0,
        )

        self.setattr_device("core")
        self.core: Core
        self.setattr_device("andor_ctrl")

        self.setattr_device("ttl_camera_exposure")
        self.ttl_camera_exposure: TTLOut

    def get_default_analyses(self):
        probability = ArrayChannel(
            TWEEZER_ROI_PROBABILITY_RESULT,
            "Per-ROI bright probability",
            element_type="float",
            shape=TWEEZER_ROI_RESULT_SHAPE,
            dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
            min=0.0,
            max=1.0,
        )
        return [
            CustomAnalysis(
                [],
                self._analyse_tweezer_roi_statistics,
                analysis_results=[
                    probability,
                    ArrayChannel(
                        TWEEZER_ROI_PROBABILITY_ERROR_RESULT,
                        "Per-ROI bright probability error",
                        element_type="float",
                        shape=TWEEZER_ROI_RESULT_SHAPE,
                        dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
                        min=0.0,
                        display_hints={"error_bar_for": probability.path},
                    ),
                    ArrayChannel(
                        TWEEZER_ROI_NUM_SHOTS_RESULT,
                        "Number of shots contributing to each ROI",
                        element_type="int",
                        shape=TWEEZER_ROI_RESULT_SHAPE,
                        dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
                        min=0,
                    ),
                    ArrayChannel(
                        TWEEZER_ROI_NUM_BRIGHT_RESULT,
                        "Number of bright shots in each ROI",
                        element_type="int",
                        shape=TWEEZER_ROI_RESULT_SHAPE,
                        dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
                        min=0,
                    ),
                ],
                online_fn=self._analyse_tweezer_roi_statistics,
                online_analysis_identifier="tweezer_roi_statistics",
            )
        ]

    def _analyse_tweezer_roi_statistics(self, axis_values, result_values, analysis_results):
        del axis_values, analysis_results

        bright = np.asarray(result_values[self.tweezer_roi_bright], dtype=np.int32)
        if bright.size == 0:
            num_bright = np.zeros(TWEEZER_ROI_RESULT_SHAPE, dtype=np.int32)
            num_shots = 0
        else:
            bright = bright.reshape((-1, NUM_TWEEZER_ROI_GROUPS, NUM_TWEEZER_ROIS))
            num_bright = np.sum(bright, axis=0, dtype=np.int32)
            num_shots = bright.shape[0]

        probability, probability_error = _estimate_binomial_probability(
            num_bright,
            num_shots,
        )
        shots_by_roi = np.full(TWEEZER_ROI_RESULT_SHAPE, num_shots, dtype=np.int32)

        return AnalysisFeedback(
            outputs={
                TWEEZER_ROI_PROBABILITY_RESULT: probability,
                TWEEZER_ROI_PROBABILITY_ERROR_RESULT: probability_error,
                TWEEZER_ROI_NUM_SHOTS_RESULT: shots_by_roi,
                TWEEZER_ROI_NUM_BRIGHT_RESULT: num_bright,
            }
        )

    def _configure_camera(self):
        configure_andor_for_rb_single_image(self.andor_ctrl)

    def host_setup(self):
        super().host_setup()
        self._configure_camera()
        _ensure_tweezer_roi_datasets(self)

    def host_cleanup(self):
        self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self.andor_ctrl.disable_em_gain()
        super().host_cleanup()

    @rpc
    def camera_start_acquisition(self):
        self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self.andor_ctrl.prepare()
        self.andor_ctrl.start_acquisition()

    @rpc
    def camera_wait_read_and_publish(self, timeout_s):
        try:
            img = self.andor_ctrl.wait_get_image16(timeout_ms=int(1000.0 * timeout_s))
        finally:
            self.andor_ctrl.abort_acquisition(ignore_idle=True)
        rois = _get_tweezer_rois(self)
        thresholds = _get_tweezer_roi_thresholds(self)
        roi_counts = _sum_tweezer_rois(img, rois)
        roi_bright = (roi_counts >= thresholds).astype(np.int32)

        self.tweezers_image.push(img)
        self.tweezer_roi_counts.push(roi_counts)
        self.tweezer_roi_bright.push(roi_bright)
        self.tweezer_roi_thresholds_applied.push(thresholds)

        self.set_dataset("andor.image", img, broadcast=True)
        self.set_dataset(TWEEZER_ROI_COUNTS_DATASET, roi_counts, broadcast=True)
        self.set_dataset(TWEEZER_ROI_BRIGHT_DATASET, roi_bright, broadcast=True)

    @kernel
    def rtio_events(self):
        self.core.break_realtime()
        delay(20.0 * ms)

        self.tweezer_tone.turn_on()
        self.Rb_MOT_loader.load_mot_on()
        delay(self.mot_hold_time.use())

        self.Rb_molasses.molasses_on()
        delay(self.molasses_time.use())

        self.Rb_cooling.cooling_on()
        delay(self.cooling_time.use())

        self.tweezer_imager.image_atoms(exposure_trigger=True)
        self.tweezer_tone.turn_off()
        delay(5*ms)

    @kernel
    def run_once(self):
        self.camera_start_acquisition()
        self.core.break_realtime()
        self.rtio_events()
        self.camera_wait_read_and_publish(self.camera_timeout.get())


LoadRbMOTToTweezersImageExp = make_fragment_prepared_dashboard_scan_exp(
    LoadRbMOTToTweezersImage,
    max_rtio_underflow_retries=0,
)
