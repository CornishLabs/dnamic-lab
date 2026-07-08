from contextlib import suppress

from artiq.coredevice.ad9910 import AD9910
from artiq.coredevice.core import Core
from artiq.coredevice.ttl import TTLOut
from artiq.coredevice.urukul import CPLD
from artiq.coredevice.zotino import Zotino

from artiq.experiment import kernel
from artiq.experiment import rpc

from artiq.language.core import delay

from artiq.language.units import MHz
from artiq.language.units import V
from artiq.language.units import dB
from artiq.language.units import ms
from artiq.language.units import s

from ndscan.define.fragment import ExpFragment
from ndscan.define.fragment import Fragment
from ndscan.define.parameters import FloatParam
from ndscan.define.parameters import FloatParamHandle
from ndscan.define.result_channels import ArrayChannel

from ndscan.runtime.api import make_fragment_prepared_dashboard_scan_exp


DDS_ATTEN_DB = 8.0 # This is what we use across all DDS Channels

# MOT stage good values
RB_COOL_DDS_FREQ_MHZ_MOT = 101.25
RB_COOL_DDS_ASF_MOT = 0.45
RB_REPUMP_DDS_FREQ_MHZ_MOT = 80.64
RB_REPUMP_DDS_ASF_MOT = 0.32
RB_EW_SHIMS_V_MOT = -0.367 
RB_UD_SHIMS_V_MOT = 0.8
RB_NS_SHIMS_V_MOT = -0.112
RB_QUAD_V_MOT = 8.8

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

        # Local variables
        self._needs_hardware_init = True
    
    def host_setup(self):
        super().host_setup()
        
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

        # DDSs
        self.dds_cpld_rb.init()
        # I am not entirely sure why, but these DDS initialisations need more
        # lack.
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

        self.dds_ch_rb_cool.set_att(DDS_ATTEN_DB)
        self.dds_ch_rb_repump.set_att(DDS_ATTEN_DB)

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


class RbLightService(Fragment):

    def build_fragment(self):
        self.setattr_param("cool_frequency",
                           FloatParam,
                           "Cool light AOM drive frequency",
                           RB_COOL_DDS_FREQ_MHZ_MOT*MHz,
                           min=(110-50)*MHz, max=(110+50)*MHz)
        self.cool_frequency: FloatParamHandle

        self.setattr_param("repump_frequency",
                           FloatParam,
                           "Repump light AOM drive frequency",
                           RB_REPUMP_DDS_FREQ_MHZ_MOT*MHz,
                           min=(80-50)*MHz, max=(80+50)*MHz)
        self.repump_frequency: FloatParamHandle

        self.setattr_param("cool_dds_amp",
                           FloatParam,
                           "Cool light AOM DDS amp (0-1)",
                           RB_COOL_DDS_ASF_MOT,
                           min=0, max=1)
        self.cool_dds_amp: FloatParamHandle

        self.setattr_param("repump_dds_amp",
                           FloatParam,
                           "Repump light AOM DDS amp (0-1)",
                           RB_REPUMP_DDS_ASF_MOT,
                           min=0, max=1)
        self.repump_dds_amp: FloatParamHandle
        
        self.setattr_param("shutter_prefire",
                           FloatParam,
                           "How much time to allow for the shutter coming on, before turning the light on",
                           10*ms,
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
        self.apply_dds_settings()
        self.turn_light_off_now(close_shutters=True)

    @kernel
    def turn_light_on_now(self, program_profile=False, pre_open_shutters=True):
        """
        Turn the cool+repump beams on `now`. This function will open the requisite shutters (if asked)
        and RF switches. It writes shutter events in the past, so be sure to have enough slack for this.
        """
        if pre_open_shutters:
            # Write shutter open into the past.
            # You must have sufficient slack for this to work.
            shutter_prefire = self.shutter_prefire.get()
            delay(-shutter_prefire) # This doesn't actually wait, just moves the cursor
            self.ttl_rb_cool_shut.on()
            self.ttl_rb_repump_shut.on()
            delay(shutter_prefire)
            # cursor is now back where this was called.
        if program_profile:
            self.apply_dds_settings()
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

    def build_fragment(self):
        self.setattr_param("EW_setpoint",
                           FloatParam,
                           "E/W Shims servo setpoint voltage",
                           RB_EW_SHIMS_V_MOT*V,
                           min=-10*V, max=+10*V)
        self.EW_setpoint: FloatParamHandle

        self.setattr_param("UD_setpoint",
                           FloatParam,
                           "U/D Shims servo setpoint voltage",
                           RB_UD_SHIMS_V_MOT*V,
                           min=-10*V, max=+10*V)
        self.UD_setpoint: FloatParamHandle
        
        self.setattr_param("NS_setpoint",
                           FloatParam,
                           "N/S Shims servo setpoint voltage",
                           RB_NS_SHIMS_V_MOT*V,
                           min=-10*V, max=+10*V)
        self.NS_setpoint: FloatParamHandle

        self.setattr_param("quad_setpoint",
                    FloatParam,
                    "Quad setpoint",
                    RB_QUAD_V_MOT*V,
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
        self.MOT_load_fields.set_setpoints()
        self.MOT_load_fields.turn_quad_on()
        self.MOT_fluoresce.turn_light_on_now(pre_open_shutters=True)

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
        ROI = (0, 511, 0, 511)  # x0, x1, y0, y1 (inclusive)

        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()

        self.andor_ctrl.set_shutter(mode=5)
        self.andor_ctrl.set_trigger_mode(7)   # external exposure
        self.andor_ctrl.set_image_region(*ROI)

    def host_setup(self):
        super().host_setup()
        self._configure_camera()

    def host_cleanup(self):
        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()
        super().host_cleanup()

    @rpc
    def camera_start_acquisition(self):
        self.andor_ctrl.start_acquisition()

    @rpc
    def camera_wait_read_and_publish(self):
        self.andor_ctrl.wait()
        img = self.andor_ctrl.get_image16()
        self.mot_image.push(img)
        self.set_dataset("andor.image", img, broadcast=True)

    @kernel
    def rtio_events(self):
        self.core.break_realtime()
        # Add enough slack for Zotino set_dac() and the shutter prefire event.
        delay(2 * ms)
        delay(self.Rb_MOT_loader.MOT_fluoresce.shutter_prefire.get())
        
        self.Rb_MOT_loader.load_mot_on()
        delay(self.Rb_MOT_preload_time.get())
        self.ttl_camera_exposure.pulse(self.exposure_time.get())
        self.Rb_MOT_loader.load_mot_off()
    
    @kernel
    def run_once(self):
        self.camera_start_acquisition()
        self.core.break_realtime()
        self.rtio_events()
        self.camera_wait_read_and_publish()


LoadRbMOTImageExp = make_fragment_prepared_dashboard_scan_exp(
    LoadRbMOTImage,
    max_rtio_underflow_retries=0,
)
