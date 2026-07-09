from artiq.coredevice.ad9910 import AD9910
from artiq.coredevice.core import Core
from artiq.coredevice.suservo import Channel as SUServoChannel
from artiq.coredevice.suservo import SUServo
from artiq.coredevice.ttl import TTLOut
from artiq.coredevice.urukul import CPLD
from artiq.coredevice.zotino import Zotino

from artiq.experiment import EnvExperiment
from artiq.experiment import kernel
from artiq.experiment import rpc

from artiq.language.core import delay
from artiq.language.units import MHz
from artiq.language.units import V
from artiq.language.units import ms
from artiq.language.units import s


# This simple diagnostic sets SUServo channel 0 as a constant RF drive for the
# tweezer/817 path. Any AWG or optical power not controlled here is assumed to
# already be set externally.

DDS_ATTEN_DB = 8.0

TWEEZER_SUSERVO_FREQ = 80.0 * MHz
TWEEZER_SUSERVO_ATTEN_DB = 8.0
TWEEZER_SUSERVO_ASF = 0.27

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
CAMERA_ROI = (0, 511, 0, 511)

SHUTTER_PREFIRE = 10.0 * ms
MOT_HOLD_TIME = 1.0 * s
MOLASSES_TIME = 30.0 * ms
COOLING_TIME = 10.0 * ms

RB_COOL_DDS_FREQ_MHZ_MOT = 101.25
RB_COOL_DDS_ASF_MOT = 0.48
RB_REPUMP_DDS_FREQ_MHZ_MOT = 80.64
RB_REPUMP_DDS_ASF_MOT = 0.32
RB_EW_SHIMS_V_MOT = -0.367
RB_UD_SHIMS_V_MOT = 0.8
RB_NS_SHIMS_V_MOT = -0.112
RB_QUAD_V_MOT = 8.8

RB_COOL_DDS_FREQ_MHZ_MOLASSES = 136.16
RB_COOL_DDS_ASF_MOLASSES = 0.67
RB_REPUMP_DDS_ASF_MOLASSES = 0.28
RB_EW_SHIMS_V_MOLASSES = -0.12
RB_UD_SHIMS_V_MOLASSES = 1.15
RB_NS_SHIMS_V_MOLASSES = 0.55

RB_COOL_DDS_FREQ_MHZ_TWEEZER_COOLING = 125.54
RB_COOL_DDS_ASF_TWEEZER_COOLING = 0.43
RB_REPUMP_DDS_ASF_TWEEZER_COOLING = 0.2

RB_COOL_DDS_FREQ_MHZ_TWEEZER_IMAGE = 103.49
RB_COOL_DDS_ASF_TWEEZER_IMAGE = 0.415
RB_REPUMP_DDS_ASF_TWEEZER_IMAGE = 0.12

RB_EW_SHIMS_V_IMAGING_COOLING = -0.05
RB_UD_SHIMS_V_IMAGING_COOLING = 1.1
RB_NS_SHIMS_V_IMAGING_COOLING = 0.1


class LoadRbMOTToTweezersImageSimple(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.core: Core

        self.setattr_device("dds_cpld_rb")
        self.dds_cpld_rb: CPLD
        self.setattr_device("dds_ch_rb_cool")
        self.dds_ch_rb_cool: AD9910
        self.setattr_device("dds_ch_rb_repump")
        self.dds_ch_rb_repump: AD9910

        self.setattr_device("zotino0")
        self.zotino0: Zotino

        self.setattr_device("ttl_camera_exposure")
        self.ttl_camera_exposure: TTLOut
        self.setattr_device("ttl_quad")
        self.ttl_quad: TTLOut
        self.setattr_device("ttl_rb_cool_shut")
        self.ttl_rb_cool_shut: TTLOut
        self.setattr_device("ttl_rb_repump_shut")
        self.ttl_rb_repump_shut: TTLOut

        # self.setattr_device("suservo0")
        # self.suservo0: SUServo
        # self.setattr_device("suservo0_ch0")
        # self.suservo0_ch0: SUServoChannel

        self.setattr_device("andor_ctrl")

    def prepare(self):
        # self.suservo_profile = self.suservo0_ch0.servo_channel
        # self.suservo_attenuator_channel = self.suservo0_ch0.servo_channel % 4
        # self.suservo_cpld = self.suservo0_ch0.dds.cpld

        # kernel_invariants = getattr(self, "kernel_invariants", set())
        # self.kernel_invariants = kernel_invariants | {
        #     "suservo_profile",
        #     "suservo_attenuator_channel",
        #     "suservo_cpld",
        # }
        pass

    @rpc
    def camera_configure(self):
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

    @rpc
    def camera_start_acquisition(self):
        self.andor_ctrl.abort_acquisition(ignore_idle=True)
        self.andor_ctrl.prepare()
        self.andor_ctrl.start_acquisition()

    @rpc
    def camera_wait_read_and_publish(self):
        try:
            img = self.andor_ctrl.wait_get_image16(
                timeout_ms=int(1000.0 * CAMERA_TIMEOUT / s)
            )
        finally:
            self.andor_ctrl.abort_acquisition(ignore_idle=True)
            self.andor_ctrl.disable_em_gain()

        self.set_dataset("andor.image", img, broadcast=True)
        self.set_dataset("rb_mot_simple.tweezers_image", img, broadcast=True)

    @kernel
    def initialise_hardware(self):
        self.core.reset()

        delay(10.0 * ms)
        self.dds_cpld_rb.init()
        self.core.break_realtime()
        delay(40.0 * ms)

        self.dds_ch_rb_cool.init()
        self.core.break_realtime()
        delay(40.0 * ms)

        self.dds_ch_rb_repump.init()
        self.core.break_realtime()

        self.zotino0.init()
        self.core.break_realtime()

        self.set_safe()
        # self.setup_tweezer_rf()

    # @kernel
    # def setup_tweezer_rf(self):
    #     # This initialises and enables the global SU-Servo engine. It will
    #     # disturb other channels if they are actively servoing.
    #     self.core.break_realtime()
    #     delay(1.0 * ms)

    #     self.suservo0.init()
    #     self.core.break_realtime()
    #     delay(1.0 * ms)

    #     self.suservo0.set_config(enable=0)
    #     self.suservo_cpld.set_att(
    #         self.suservo_attenuator_channel,
    #         TWEEZER_SUSERVO_ATTEN_DB,
    #     )
    #     self.suservo0_ch0.set_y(
    #         profile=self.suservo_profile,
    #         y=TWEEZER_SUSERVO_ASF,
    #     )
    #     self.suservo0_ch0.set_dds(
    #         profile=self.suservo_profile,
    #         frequency=TWEEZER_SUSERVO_FREQ,
    #         offset=0.0,
    #     )
    #     self.suservo0_ch0.set(
    #         en_out=1,
    #         en_iir=0,
    #         profile=self.suservo_profile,
    #     )
    #     self.suservo0.set_config(enable=1)
    #     self.core.break_realtime()

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

    @kernel
    def run_rtio_sequence(self):
        self.core.break_realtime()
        delay(20.0 * ms)

        # MOT loading.
        self.dds_ch_rb_cool.set(
            RB_COOL_DDS_FREQ_MHZ_MOT * MHz,
            amplitude=RB_COOL_DDS_ASF_MOT,
        )
        self.dds_ch_rb_repump.set(
            RB_REPUMP_DDS_FREQ_MHZ_MOT * MHz,
            amplitude=RB_REPUMP_DDS_ASF_MOT,
        )
        self.zotino0.set_dac(
            [
                RB_EW_SHIMS_V_MOT * V,
                RB_UD_SHIMS_V_MOT * V,
                RB_NS_SHIMS_V_MOT * V,
                RB_QUAD_V_MOT * V,
            ],
            [0, 1, 2, 3],
        )
        self.ttl_quad.on()
        self.ttl_rb_cool_shut.on()
        self.ttl_rb_repump_shut.on()
        delay(SHUTTER_PREFIRE)
        self.dds_ch_rb_cool.sw.on()
        self.dds_ch_rb_repump.sw.on()
        # self.ttl_camera_exposure.on()
        delay(MOT_HOLD_TIME)
        # self.ttl_camera_exposure.off()

        # Molasses.
        self.zotino0.set_dac(
            [
                RB_EW_SHIMS_V_MOLASSES * V,
                RB_UD_SHIMS_V_MOLASSES * V,
                RB_NS_SHIMS_V_MOLASSES * V,
                0.0 * V,
            ],
            [0, 1, 2, 3],
        )
        delay(0.5*ms)
        self.dds_ch_rb_cool.set(
            RB_COOL_DDS_FREQ_MHZ_MOLASSES * MHz,
            amplitude=RB_COOL_DDS_ASF_MOLASSES,
        )
        self.dds_ch_rb_repump.set(
            RB_REPUMP_DDS_FREQ_MHZ_MOT * MHz,
            amplitude=RB_REPUMP_DDS_ASF_MOLASSES,
        )
        delay(0.5*ms)
        self.ttl_quad.off()
        delay(MOLASSES_TIME)

        # Cooling in the tweezer.
        self.zotino0.set_dac(
            [
                RB_EW_SHIMS_V_IMAGING_COOLING * V,
                RB_UD_SHIMS_V_IMAGING_COOLING * V,
                RB_NS_SHIMS_V_IMAGING_COOLING * V,
                0.0 * V,
            ],
            [0, 1, 2, 3],
        )
        self.dds_ch_rb_cool.set(
            RB_COOL_DDS_FREQ_MHZ_TWEEZER_COOLING * MHz,
            amplitude=RB_COOL_DDS_ASF_TWEEZER_COOLING,
        )
        self.dds_ch_rb_repump.set(
            RB_REPUMP_DDS_FREQ_MHZ_MOT * MHz,
            amplitude=RB_REPUMP_DDS_ASF_TWEEZER_COOLING,
        )
        delay(COOLING_TIME)

        # Image atoms in the tweezer.
        self.dds_ch_rb_cool.set(
            RB_COOL_DDS_FREQ_MHZ_TWEEZER_IMAGE * MHz,
            amplitude=RB_COOL_DDS_ASF_TWEEZER_IMAGE,
        )
        self.dds_ch_rb_repump.set(
            RB_REPUMP_DDS_FREQ_MHZ_MOT * MHz,
            amplitude=RB_REPUMP_DDS_ASF_TWEEZER_IMAGE,
        )
        self.ttl_camera_exposure.on()
        delay(CAMERA_EXPOSURE_TIME)
        self.ttl_camera_exposure.off()

        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()
        self.set_safe()

    @kernel
    def run(self):
        self.camera_configure()
        self.initialise_hardware()
        self.camera_start_acquisition()
        self.core.break_realtime()
        self.run_rtio_sequence()
        self.camera_wait_read_and_publish()
