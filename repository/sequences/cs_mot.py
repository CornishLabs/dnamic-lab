import numpy as np

from dnamic_toolkit.imaging.binomial import estimate_probability_array
from dnamic_toolkit.imaging.rois import sum_counts_in_rois
from dnamic_toolkit.imaging.rois import threshold_counts_to_occupancy

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
from ndscan.define.parameters import IntParam
from ndscan.define.parameters import IntParamHandle
from ndscan.define.result_channels import ArrayChannel
from ndscan.define.result_channels import FloatChannel

from ndscan.runtime.api import ExecutionPolicy
from ndscan.runtime.api import ScanRequest
from ndscan.runtime.api import make_fragment_prepared_dashboard_scan_exp
from ndscan.runtime.api import prepare_child_scan


DDS_ATTEN_DB = 8.0 # This is what we use across all DDS Channels
TWEEZER_SUSERVO_FREQ = 80.0 * MHz
TWEEZER_SUSERVO_ATTEN_DB = 8.0
TWEEZER_SUSERVO_ADC_CHANNEL = 1
TWEEZER_SUSERVO_PGIA_GAIN = 0
# TWEEZER_SUSERVO_TARGET_V = 1.15
# TWEEZER_SUSERVO_OFFSET = -TWEEZER_SUSERVO_TARGET_V * (
#     10.0 ** (TWEEZER_SUSERVO_PGIA_GAIN - 1)
# )
TWEEZER_SUSERVO_KP = -0.25
TWEEZER_SUSERVO_KI = -15_000.0
TWEEZER_SUSERVO_GAIN_LIMIT = 0.0

TWEEZER_MOT_SETPOINT = 6.4
TWEEZER_IMAGING_SETPOINT = 5.4

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
MOT_HOLD_TIME = 0.3 * s
MOLASSES_TIME = 35.0 * ms
COOLING_TIME = 10.0 * ms
SHOTS_PER_POINT = 50

NUM_TWEEZER_ROIS = 9
NUM_TWEEZER_ROI_GROUPS = 1
TWEEZER_ROI_RESULT_SHAPE = (NUM_TWEEZER_ROI_GROUPS, NUM_TWEEZER_ROIS)
TWEEZER_ROI_RESULT_DIM_NAMES = ("group", "roi")

# ``live.*`` datasets are temporary, two-way state shared with dashboard applets.
# This experiment produces one image per shot, so it owns slot 0 and resets that
# slot's validity before every acquisition. The ROI and threshold datasets are
# deliberately separate from the image: the ROI applet can move them while a
# repeated experiment is running, and the next shot will read the new values.
LIVE_IMAGING_EXPECTED_IMAGES_DATASET = "live.imaging.expected_images"
LIVE_IMAGING_SLOT_0_VALID_DATASET = "live.imaging.slot0.valid"
LIVE_IMAGING_SLOT_0_IMAGE_DATASET = "live.imaging.slot0.image"
TWEEZER_ROIS_DATASET = "live.imaging.slot0.rois"
TWEEZER_ROI_THRESHOLDS_DATASET = "live.imaging.slot0.thresholds"
TWEEZER_ROI_PROBABILITY_RESULT = "tweezer_roi_bright_probability"
TWEEZER_ROI_PROBABILITY_ERROR_RESULT = "tweezer_roi_bright_probability_error"
TWEEZER_AVERAGE_PROBABILITY_RESULT = "tweezer_average_bright_probability"
TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT = "tweezer_average_bright_probability_error"
TWEEZER_ROI_NUM_SHOTS_RESULT = "tweezer_roi_num_shots"
TWEEZER_ROI_NUM_BRIGHT_RESULT = "tweezer_roi_num_bright"

# These ROI bounds are Python-style half-open bounds: (y0, y1, x0, x1).
# The extra outer list is the ROI "group" dimension expected by image_roi_applet.py.
ROI_SPACING=7.25
DEFAULT_TWEEZER_ROIS = (
    tuple((round(258-ROI_SPACING*i), round(258-ROI_SPACING*i)+3, 171, 174) for i in range(9)),
)

# Integrated-count thresholds for the five ROIs. This deliberately starts
# uncalibrated; replace it with thresholds obtained from real empty/loaded shots.
DEFAULT_TWEEZER_ROI_THRESHOLDS = (tuple(9000.0 for i in range(9)),)


def _get_tweezer_rois(owner):
    rois = owner.get_dataset(TWEEZER_ROIS_DATASET, archive=False)
    return np.asarray(rois, dtype=np.int32)


def _get_tweezer_roi_thresholds(owner):
    thresholds = owner.get_dataset(TWEEZER_ROI_THRESHOLDS_DATASET, archive=False)
    thresholds = np.asarray(thresholds, dtype=np.float64)
    if thresholds.shape == (NUM_TWEEZER_ROIS,):
        thresholds = thresholds.reshape(TWEEZER_ROI_RESULT_SHAPE)
    return thresholds


def _initialise_live_imaging_datasets(owner):
    """Initialise this experiment's one live-image slot.

    A fragment instance may enter ``host_setup`` more than once when used in a
    detached child scan. The instance-local guard ensures that those later entries
    do not restore the defaults over a manual ROI edit.
    """
    if owner._live_imaging_initialised:
        return

    owner.set_dataset(
        LIVE_IMAGING_SLOT_0_VALID_DATASET,
        False,
        broadcast=True,
        persist=False,
        archive=False,
    )
    owner.set_dataset(
        TWEEZER_ROIS_DATASET,
        np.asarray(DEFAULT_TWEEZER_ROIS, dtype=np.int32),
        broadcast=True,
        persist=False,
        archive=False,
    )
    owner.set_dataset(
        TWEEZER_ROI_THRESHOLDS_DATASET,
        np.asarray(DEFAULT_TWEEZER_ROI_THRESHOLDS, dtype=np.float64),
        broadcast=True,
        persist=False,
        archive=False,
    )
    owner.set_dataset(
        LIVE_IMAGING_EXPECTED_IMAGES_DATASET,
        1,
        broadcast=True,
        persist=False,
        archive=False,
    )
    owner._live_imaging_initialised = True


def _set_live_image_valid(owner, valid):
    """Set slot 0's small best-effort validity/commit flag."""
    owner.set_dataset(
        LIVE_IMAGING_SLOT_0_VALID_DATASET,
        bool(valid),
        broadcast=True,
        persist=False,
        archive=False,
    )


def _publish_live_image(owner, image):
    """Publish slot 0, writing ``valid`` last as its commit marker."""
    owner.set_dataset(
        LIVE_IMAGING_SLOT_0_IMAGE_DATASET,
        image,
        broadcast=True,
        persist=False,
        archive=False,
    )
    _set_live_image_valid(owner, True)


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
CS_COOL_DDS_FREQ_MHZ_MOT = 99.45
CS_COOL_DDS_ASF_MOT = 0.58
CS_REPUMP_DDS_FREQ_MHZ_MOT = 94.17
CS_REPUMP_DDS_ASF_MOT = 0.36
CS_EW_SHIMS_V_MOT = 0.04 
CS_UD_SHIMS_V_MOT = 0.51   # was  0.379    
CS_NS_SHIMS_V_MOT = -0.25  # was -0.337
CS_QUAD_V_MOT = 8.8

# Initial transfer/imaging values from the old control system. ASFs are scaled
# from the MOT dBm values, assuming DDS ASF is an RF voltage amplitude.
CS_COOL_DDS_FREQ_MHZ_MOLASSES = 114.2 # CHECKED
CS_COOL_DDS_ASF_MOLASSES = 0.58
CS_REPUMP_DDS_ASF_MOLASSES = 0.37
CS_EW_SHIMS_V_MOLASSES = -0.15
CS_UD_SHIMS_V_MOLASSES = 1.05
CS_NS_SHIMS_V_MOLASSES = 0.5


# COOLING STAGE BEFORE IMAGING
CS_COOL_DDS_FREQ_MHZ_TWEEZER_COOLING = 119.0  # CHECKED
CS_COOL_DDS_ASF_TWEEZER_COOLING = 0.6
CS_REPUMP_DDS_ASF_TWEEZER_COOLING= 0.2

# IMAGING
CS_COOL_DDS_FREQ_MHZ_TWEEZER_IMAGE = 105.0  # CHECKED
CS_COOL_DDS_ASF_TWEEZER_IMAGE = 0.58
CS_REPUMP_DDS_ASF_TWEEZER_IMAGE = 0.2

CS_EW_SHIMS_V_IMAGING_COOLING = -0.15  #CHECKED
CS_UD_SHIMS_V_IMAGING_COOLING = 1.15
CS_NS_SHIMS_V_IMAGING_COOLING = 0.5

class HardwareInitOnce(Fragment):
    # TODO: Eventually this fragment should not manually enumerate the names of the
    #       hardware, instead it should lookup the device db names.

    def build_fragment(self):
        self.setattr_device("core")
        self.core: Core

        # DDSs
        self.setattr_device("dds_cpld_cs")
        self.dds_cpld_cs: CPLD
        self.setattr_device("dds_ch_cs_cool")
        self.dds_ch_cs_cool: AD9910
        self.setattr_device("dds_ch_cs_repump")
        self.dds_ch_cs_repump: AD9910

        # DAC
        self.setattr_device("zotino0")
        self.zotino0: Zotino

        # SUServo
        self.setattr_device("suservo0")
        self.suservo0: SUServo
        self.setattr_device("suservo0_ch1")
        self.suservo0_ch1: SUServoChannel

        # Local variables
        self._needs_hardware_init = True
    
    def host_setup(self):
        super().host_setup()

        self.suservo_profile = self.suservo0_ch1.servo_channel
        self.suservo_attenuator_channel = self.suservo0_ch1.servo_channel % 4
        self.suservo_cpld = self.suservo0_ch1.dds.cpld

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {
            "suservo0",
            "suservo0_ch1",
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
        self.dds_cpld_cs.init()
        # I am not entirely sure why, but these DDS initialisations need more
        # slack.
        ## Maybe the channel initialisations wait an indeterminate amount of time 
        #    due to a PLL lock check, so break_realtime afterwords to stop
        #    pseudorandom RTIOUnderflow.
        self.core.break_realtime()
        delay(40*ms)  
        self.dds_ch_cs_cool.init()
        self.core.break_realtime()
        delay(40*ms)     
        self.dds_ch_cs_repump.init()
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
        self.suservo0_ch1.set_iir(
            profile=self.suservo_profile,
            adc=TWEEZER_SUSERVO_ADC_CHANNEL,
            kp=TWEEZER_SUSERVO_KP,
            ki=TWEEZER_SUSERVO_KI,
            g=TWEEZER_SUSERVO_GAIN_LIMIT,
        )
        self.suservo0_ch1.set_dds(
            profile=self.suservo_profile,
            frequency=TWEEZER_SUSERVO_FREQ,
            offset=0.0,
        )
        self.suservo0_ch1.set_y( # Set integrator (output) to zero
            profile=self.suservo_profile,
            y=0.0,
        )
        self.suservo0_ch1.set(
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
        self.setattr_device("ttl_cs_cool_shut")
        self.ttl_cs_cool_shut: TTLOut
        self.setattr_device("ttl_cs_repump_shut")
        self.ttl_cs_repump_shut: TTLOut

        self.setattr_device("dds_ch_cs_cool")
        self.dds_ch_cs_cool: AD9910
        self.setattr_device("dds_ch_cs_repump")
        self.dds_ch_cs_repump: AD9910

        self.setattr_device("zotino0")
        self.zotino0: Zotino

        # SUServo
        self.setattr_device("suservo0")
        self.suservo0: SUServo
        self.setattr_device("suservo0_ch1")
        self.suservo0_ch1: SUServoChannel

    def host_setup(self):
        super().host_setup()

        self.suservo_profile = self.suservo0_ch1.servo_channel
        self.suservo_attenuator_channel = self.suservo0_ch1.servo_channel % 4
        self.suservo_cpld = self.suservo0_ch1.dds.cpld

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {
            "suservo0",
            "suservo0_ch1",
            "suservo_profile",
            "suservo_attenuator_channel",
            "suservo_cpld",
        }


    @kernel
    def set_safe(self):
        self.core.break_realtime()

        self.ttl_camera_exposure.off()
        self.ttl_quad.off()
        self.ttl_cs_cool_shut.off()
        self.ttl_cs_repump_shut.off()
        self.dds_ch_cs_cool.sw.off()
        self.dds_ch_cs_repump.sw.off()

        self.zotino0.set_dac(
            [0.0 * V, 0.0 * V, 0.0 * V, 0.0 * V],
            [0, 1, 2, 3],
        )

        delay(1*ms)
        self.dds_ch_cs_cool.set_att(DDS_ATTEN_DB)
        self.dds_ch_cs_repump.set_att(DDS_ATTEN_DB)

        self.suservo0.set_config(enable=0)
        self.suservo0_ch1.set_y( # Set integrator (output) to zero
            profile=self.suservo_profile,
            y=0.0,
        )
        self.suservo0_ch1.set(
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
    """Servoed SU-Servo channel 1 output used as the tweezer RF drive."""

    def build_fragment(self,
                       setpoint_default=TWEEZER_MOT_SETPOINT):
        self.setattr_device("core")
        self.core: Core

        self.setattr_device("suservo0")
        self.suservo0: SUServo
        self.setattr_device("suservo0_ch1")
        self.suservo0_ch1: SUServoChannel

        self.setattr_param("setpoint",
                    FloatParam,
                    "1066 Setpoint",
                    setpoint_default,
                    min=0.0, max=10.0)
        self.setpoint: FloatParamHandle

    def host_setup(self):
        super().host_setup()

        self.suservo_profile = self.suservo0_ch1.servo_channel
        self.suservo_attenuator_channel = self.suservo0_ch1.servo_channel % 4
        self.suservo_cpld = self.suservo0_ch1.dds.cpld

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {
            "suservo0",
            "suservo0_ch1",
            "suservo_profile",
            "suservo_attenuator_channel",
            "suservo_cpld",
        }

    @kernel
    def setpoint_v_to_offset(self, setpoint_v):
        """Convert physical ADC input volts to normalized SUServo offset."""
        pgia_gain = 10.0**TWEEZER_SUSERVO_PGIA_GAIN
        return -setpoint_v * pgia_gain / 10.24

    @kernel
    def set_rf_iir(self, rf_sw=1, en_iir=1):
        self.suservo0_ch1.set(
            en_out=rf_sw,
            en_iir=en_iir,
            profile=self.suservo_profile,
        )
    
    @kernel
    def set_setpoint(self):
        self.suservo0_ch1.set_dds_offset(
            profile=self.suservo_profile,
            offset=self.setpoint_v_to_offset(self.setpoint.use()),
        )


class CsLightService(Fragment):

    def build_fragment(
        self,
        cool_frequency_default=CS_COOL_DDS_FREQ_MHZ_MOT*MHz,
        repump_frequency_default=CS_REPUMP_DDS_FREQ_MHZ_MOT*MHz,
        cool_dds_amp_default=CS_COOL_DDS_ASF_MOT,
        repump_dds_amp_default=CS_REPUMP_DDS_ASF_MOT,
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
        self.setattr_device("dds_ch_cs_cool")
        self.dds_ch_cs_cool: AD9910
        self.setattr_device("dds_ch_cs_repump")
        self.dds_ch_cs_repump: AD9910
        self.setattr_device("dds_cpld_cs")
        self.dds_cpld_cs: CPLD
        self.setattr_device("ttl_cs_cool_shut")
        self.ttl_cs_cool_shut: TTLOut
        self.setattr_device("ttl_cs_repump_shut")
        self.ttl_cs_repump_shut: TTLOut
    
    # --- In seq action funcs ---

    @kernel
    def apply_dds_settings(self):
        """
        Changes the DDS parameters to a different freq/amp.
        This function can be used alone to just change the beam freq/amp
        without changing the state of the RF switches.
        """
        self.dds_ch_cs_cool.set(self.cool_frequency.use(), amplitude=self.cool_dds_amp.use())
        self.dds_ch_cs_repump.set(self.repump_frequency.use(), amplitude=self.repump_dds_amp.use())

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
            self.ttl_cs_cool_shut.on()
            self.ttl_cs_repump_shut.on()
            delay(shutter_prefire)
        # If the switch was already on, this a Noop
        self.dds_ch_cs_cool.sw.on()
        self.dds_ch_cs_repump.sw.on()
    
    @kernel
    def turn_light_off_now(self, close_shutters=True):
        """
        Turn the cool+repump beams off `now`. This function will close the shutters (if asked)
        and RF switches.
        """
        self.dds_ch_cs_cool.sw.off()
        self.dds_ch_cs_repump.sw.off()
        if close_shutters:
            self.ttl_cs_cool_shut.off()
            self.ttl_cs_repump_shut.off()

class LowBFieldService(Fragment):
    """
    Set the shim fields to the given setpoints. This currently uses the DAC to send voltages to
    the control drivers.
    """

    def build_fragment(
        self,
        EW_setpoint_default=CS_EW_SHIMS_V_MOT*V,
        UD_setpoint_default=CS_UD_SHIMS_V_MOT*V,
        NS_setpoint_default=CS_NS_SHIMS_V_MOT*V,
        quad_setpoint_default=CS_QUAD_V_MOT*V,
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


class CsMOTLoadService(Fragment):
    def build_fragment(self):
        self.setattr_fragment("MOT_load_fields", LowBFieldService)
        self.MOT_load_fields: LowBFieldService
        self.setattr_fragment("MOT_fluoresce", CsLightService)
        self.MOT_fluoresce: CsLightService

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


class LoadCsMOTImage(ExpFragment):
    def build_fragment(self):
        self._live_imaging_initialised = False

        self.setattr_fragment("known_hardware_state", KnownHardwareState)
        self.known_hardware_state: KnownHardwareState

        self.setattr_fragment("Cs_MOT_loader", CsMOTLoadService)
        self.Cs_MOT_loader: CsMOTLoadService

        self.setattr_param("Cs_MOT_preload_time",
                           FloatParam,
                           "How long to load the MOT for before starting exposing the camera",
                           1.0*s,
                           min=1.0*ms,max=10.0*s)
        self.Cs_MOT_preload_time:FloatParamHandle

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
        _initialise_live_imaging_datasets(self)

    def host_cleanup(self):
        self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self.andor_ctrl.disable_em_gain()
        super().host_cleanup()

    @rpc
    def camera_start_acquisition(self):
        _set_live_image_valid(self, False)
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
        _publish_live_image(self, img)

    @kernel
    def rtio_events(self):
        self.core.break_realtime()
        delay(20.0 * ms)
        
        self.Cs_MOT_loader.load_mot_on()
        delay(self.Cs_MOT_preload_time.get())
        self.ttl_camera_exposure.pulse(self.exposure_time.get())
        self.Cs_MOT_loader.load_mot_off()
        self.known_hardware_state.safe_state.set_safe()
    
    @kernel
    def run_once(self):
        self.camera_start_acquisition()
        self.core.break_realtime()
        self.rtio_events()
        self.camera_wait_read_and_publish(self.camera_timeout.get())


LoadCsMOTImageExp = make_fragment_prepared_dashboard_scan_exp(
    LoadCsMOTImage,
    max_rtio_underflow_retries=0,
)


class CsMolassesService(Fragment):
    def build_fragment(self):
        self.setattr_fragment(
            "molasses_fields",
            LowBFieldService,
            CS_EW_SHIMS_V_MOLASSES*V,
            CS_UD_SHIMS_V_MOLASSES*V,
            CS_NS_SHIMS_V_MOLASSES*V,
            0.0*V,
        )
        self.molasses_fields: LowBFieldService

        self.setattr_fragment(
            "molasses_light",
            CsLightService,
            CS_COOL_DDS_FREQ_MHZ_MOLASSES*MHz,
            CS_REPUMP_DDS_FREQ_MHZ_MOT*MHz,
            CS_COOL_DDS_ASF_MOLASSES,
            CS_REPUMP_DDS_ASF_MOLASSES,
        )
        self.molasses_light: CsLightService

    @kernel
    def molasses_on(self):
        self.molasses_fields.set_setpoints()
        delay(0.5*ms)
        self.molasses_light.apply_dds_settings()
        delay(0.5*ms)
        self.molasses_fields.turn_quad_off()

class CsCoolingService(Fragment):
    def build_fragment(self):
        self.setattr_fragment(
            "cooling_fields",
            LowBFieldService,
            CS_EW_SHIMS_V_IMAGING_COOLING*V,
            CS_UD_SHIMS_V_IMAGING_COOLING*V,
            CS_NS_SHIMS_V_IMAGING_COOLING*V,
            0.0*V,
        )
        self.cooling_fields: LowBFieldService

        self.setattr_fragment(
            "cooling_light",
            CsLightService,
            CS_COOL_DDS_FREQ_MHZ_TWEEZER_COOLING*MHz,
            CS_REPUMP_DDS_FREQ_MHZ_MOT*MHz,
            CS_COOL_DDS_ASF_TWEEZER_COOLING,
            CS_REPUMP_DDS_ASF_TWEEZER_COOLING,
        )
        self.cooling_light: CsLightService

    @kernel
    def cooling_on(self):
        self.cooling_fields.set_setpoints()
        self.cooling_light.apply_dds_settings()

class CsTweezerImageService(Fragment):
    def build_fragment(self):
        self.setattr_fragment(
            "imaging_light",
            CsLightService,
            CS_COOL_DDS_FREQ_MHZ_TWEEZER_IMAGE*MHz,
            CS_REPUMP_DDS_FREQ_MHZ_MOT*MHz,
            CS_COOL_DDS_ASF_TWEEZER_IMAGE,
            CS_REPUMP_DDS_ASF_TWEEZER_IMAGE,
        )
        self.imaging_light: CsLightService

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


class LoadCsMOTToTweezersImage(ExpFragment):
    def build_fragment(self):
        self._live_imaging_initialised = False

        self.setattr_fragment("known_hardware_state", KnownHardwareState)
        self.known_hardware_state: KnownHardwareState

        self.setattr_fragment("tweezer_tone_mot", TweezerSUServoToneService,
                              TWEEZER_MOT_SETPOINT)
        self.tweezer_tone_mot: TweezerSUServoToneService

        self.setattr_fragment("Cs_MOT_loader", CsMOTLoadService)
        self.Cs_MOT_loader: CsMOTLoadService

        self.setattr_fragment("Cs_molasses", CsMolassesService)
        self.Cs_molasses: CsMolassesService


        self.setattr_fragment("tweezer_tone_cool_im", TweezerSUServoToneService,
                              TWEEZER_IMAGING_SETPOINT)
        self.tweezer_tone_cool_im: TweezerSUServoToneService

        self.setattr_fragment("Cs_cooling", CsCoolingService)
        self.Cs_cooling: CsCoolingService

        self.setattr_fragment("tweezer_imager", CsTweezerImageService)
        self.tweezer_imager: CsTweezerImageService

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
        average_probability = FloatChannel(
            TWEEZER_AVERAGE_PROBABILITY_RESULT,
            "Mean bright probability across all tweezer ROIs",
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
                    average_probability,
                    FloatChannel(
                        TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT,
                        "Mean bright probability error across all tweezer ROIs",
                        min=0.0,
                        display_hints={"error_bar_for": average_probability.path},
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

        probability, probability_error = estimate_probability_array(
            num_bright,
            num_shots,
        )
        average_probability = float(np.mean(probability))
        average_probability_error = float(
            np.sqrt(np.sum(probability_error**2)) / probability_error.size
        )
        shots_by_roi = np.full(TWEEZER_ROI_RESULT_SHAPE, num_shots, dtype=np.int32)

        return AnalysisFeedback(
            outputs={
                TWEEZER_ROI_PROBABILITY_RESULT: probability,
                TWEEZER_ROI_PROBABILITY_ERROR_RESULT: probability_error,
                TWEEZER_AVERAGE_PROBABILITY_RESULT: average_probability,
                TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT: average_probability_error,
                TWEEZER_ROI_NUM_SHOTS_RESULT: shots_by_roi,
                TWEEZER_ROI_NUM_BRIGHT_RESULT: num_bright,
            }
        )

    def _configure_camera(self):
        configure_andor_for_rb_single_image(self.andor_ctrl)

    def host_setup(self):
        super().host_setup()
        self._configure_camera()
        _initialise_live_imaging_datasets(self)

    def host_cleanup(self):
        self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self.andor_ctrl.disable_em_gain()
        super().host_cleanup()

    @rpc
    def camera_start_acquisition(self):
        _set_live_image_valid(self, False)
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
        roi_counts = sum_counts_in_rois(img, rois, dtype=np.int64)
        roi_bright = threshold_counts_to_occupancy(roi_counts, thresholds).astype(np.int32)

        self.tweezers_image.push(img)
        self.tweezer_roi_counts.push(roi_counts)
        self.tweezer_roi_bright.push(roi_bright)
        self.tweezer_roi_thresholds_applied.push(thresholds)

        _publish_live_image(self, img)

    @kernel
    def rtio_events(self):
        self.core.break_realtime()
        delay(20.0 * ms)

        self.tweezer_tone_mot.set_rf_iir(rf_sw=1, en_iir=1)
        self.tweezer_tone_mot.set_setpoint()
        # self.ttl_camera_exposure.on()
        self.Cs_MOT_loader.load_mot_on()
        delay(self.mot_hold_time.use())
        # self.ttl_camera_exposure.off()

        self.Cs_molasses.molasses_on()
        delay(self.molasses_time.use())
        self.tweezer_tone_cool_im.set_setpoint()
        self.Cs_cooling.cooling_on()
        delay(self.cooling_time.use())

        self.tweezer_imager.image_atoms(exposure_trigger=True)
        self.tweezer_tone_mot.set_rf_iir(rf_sw=0, en_iir=0)
        delay(5*ms)

    @kernel
    def run_once(self):
        self.camera_start_acquisition()
        self.core.break_realtime()
        self.rtio_events()
        self.camera_wait_read_and_publish(self.camera_timeout.get())


LoadCsMOTToTweezersImageExp = make_fragment_prepared_dashboard_scan_exp(
    LoadCsMOTToTweezersImage,
    max_rtio_underflow_retries=0,
)


class LoadCsMOTToTweezersImageStatistics(ExpFragment):
    """Repeat the tweezer-image shot and publish one probability point."""

    def build_fragment(self):
        self.setattr_fragment("shot", LoadCsMOTToTweezersImage, detached=True)
        self.shot: LoadCsMOTToTweezersImage

        self.repeat_scan = prepare_child_scan(
            self,
            self.shot,
            name="repeat_scan",
            max_rtio_underflow_retries=0,
        )

        self.setattr_param(
            "shots_per_point",
            IntParam,
            "Raw shots to average into one scan point",
            SHOTS_PER_POINT,
            min=1,
        )
        self.shots_per_point: IntParamHandle

        probability = self.setattr_result(
            TWEEZER_ROI_PROBABILITY_RESULT,
            ArrayChannel,
            "Per-ROI bright probability",
            element_type="float",
            shape=TWEEZER_ROI_RESULT_SHAPE,
            dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
            min=0.0,
            max=1.0,
        )
        probability_error = self.setattr_result(
            TWEEZER_ROI_PROBABILITY_ERROR_RESULT,
            ArrayChannel,
            "Per-ROI bright probability error",
            element_type="float",
            shape=TWEEZER_ROI_RESULT_SHAPE,
            dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
            min=0.0,
            display_hints={"error_bar_for": probability.path},
        )
        average_probability = self.setattr_result(
            TWEEZER_AVERAGE_PROBABILITY_RESULT,
            FloatChannel,
            "Mean bright probability across all tweezer ROIs",
            min=0.0,
            max=1.0,
        )
        self._stat_channels = {
            TWEEZER_ROI_PROBABILITY_RESULT: probability,
            TWEEZER_ROI_PROBABILITY_ERROR_RESULT: probability_error,
            TWEEZER_AVERAGE_PROBABILITY_RESULT: average_probability,
            TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT: self.setattr_result(
                TWEEZER_AVERAGE_PROBABILITY_ERROR_RESULT,
                FloatChannel,
                "Mean bright probability error across all tweezer ROIs",
                min=0.0,
                display_hints={"error_bar_for": average_probability.path},
            ),
            TWEEZER_ROI_NUM_SHOTS_RESULT: self.setattr_result(
                TWEEZER_ROI_NUM_SHOTS_RESULT,
                ArrayChannel,
                "Number of shots contributing to each ROI",
                element_type="int",
                shape=TWEEZER_ROI_RESULT_SHAPE,
                dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
                min=0,
            ),
            TWEEZER_ROI_NUM_BRIGHT_RESULT: self.setattr_result(
                TWEEZER_ROI_NUM_BRIGHT_RESULT,
                ArrayChannel,
                "Number of bright shots in each ROI",
                element_type="int",
                shape=TWEEZER_ROI_RESULT_SHAPE,
                dim_names=TWEEZER_ROI_RESULT_DIM_NAMES,
                min=0,
            ),
        }

    def run_once(self):
        num_shots = int(self.shots_per_point.get())
        self.repeat_scan.configure(
            ScanRequest.single(
                execution_policy=ExecutionPolicy(max_points_per_batch=min(num_shots, 16))
            ).with_repeats(repeats=num_shots)
        )
        outputs = self.repeat_scan.execute()
        for name, channel in self._stat_channels.items():
            channel.push(outputs[name])


class LoadCsMOTToTweezersImageStatisticsDashboard(
    LoadCsMOTToTweezersImageStatistics
):
    """Dashboard wrapper for scanning averaged tweezer-loading statistics."""

    def get_always_shown_params(self):
        shown = super().get_always_shown_params()
        shown += [
            self.shots_per_point,
            self.shot.mot_hold_time,
            self.shot.molasses_time,
            self.shot.cooling_time,
            self.shot.camera_timeout,
            self.shot.Cs_cooling.cooling_light.cool_frequency,
            self.shot.Cs_cooling.cooling_light.cool_dds_amp,
            self.shot.tweezer_imager.exposure_time,
        ]
        return shown


LoadCsMOTToTweezersImageStatisticsExp = make_fragment_prepared_dashboard_scan_exp(
    LoadCsMOTToTweezersImageStatisticsDashboard,
    max_rtio_underflow_retries=0,
)
