from ndscan.experiment import *
from contextlib import suppress

# core device drivers
from artiq.coredevice.core import Core
from artiq.coredevice.ad9910 import AD9910 # This is per ch
from artiq.coredevice.urukul import CPLD # This is the whole urukul controller
from artiq.coredevice.ttl import TTLOut
from artiq.coredevice.zotino import Zotino
from artiq.coredevice.sampler import Sampler

from ndscan.experiment.parameters import FloatParamHandle

class Fluoresce(Fragment):

    def build_fragment(self):
        self.setattr_param("cool_frequency",
                           FloatParam,
                           "Cool light AOM drive frequency",
                           110*MHz,
                           min=(110-50)*MHz, max=(110+50)*MHz)
        self.cool_frequency: FloatParamHandle

        self.setattr_param("repump_frequency",
                           FloatParam,
                           "Repump light AOM drive frequency",
                           110*MHz,
                           min=(110-50)*MHz, max=(110+50)*MHz)
        self.repump_frequency: FloatParamHandle

        self.setattr_param("cool_dds_amp",
                           FloatParam,
                           "Cool light AOM DDS amp (0-1)",
                           0.6,
                           min=0, max=1)
        self.cool_dds_amp: FloatParamHandle

        self.setattr_param("repump_dds_amp",
                           FloatParam,
                           "Repump light AOM DDS amp (0-1)",
                           0.6,
                           min=0, max=1)
        self.repump_dds_amp: FloatParamHandle

        self.setattr_param("cool_dds_att",
                           FloatParam,
                           "Cool light AOM DDS attenuator",
                           3.0*dB,
                           min=0*dB, max=30*dB)
        self.cool_dds_att: FloatParamHandle

        self.setattr_param("repump_dds_att",
                           FloatParam,
                           "Repump light AOM DDS attenuator",
                           3.0*dB,
                           min=0*dB, max=30*dB)
        self.repump_dds_att: FloatParamHandle
        
        self.setattr_param("cool_shutter_prefire",
                           FloatParam,
                           "How much time to allow for the shutter coming on, before turning the light on",
                           10*ms,
                           min=0*ms, max=200*ms)
        self.cool_shutter_prefire: FloatParamHandle
        
        self.setattr_param("repump_shutter_prefire",
                           FloatParam,
                           "How much time to allow for the shutter coming on, before turning the light on",
                           10*ms,
                           min=0*ms, max=200*ms)
        self.repump_shutter_prefire: FloatParamHandle
        
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
    


    @kernel
    def rtio_init_once(self):
        # If some host logic was just run, we need to make sure the timeline is valid
        self.core.break_realtime()
        # Initialise
        self.dds_cpld_rb.init()
        self.dds_ch_rb_cool.init()
        self.dds_ch_rb_repump.init()


    def host_setup(self): # Run once at the start of exp alongside all other host_setups, recursively.
        self.rtio_init_once()
         # To continue to initialise all subfragments, invoke the parent implementation:
        super().host_setup()

    @kernel
    def device_setup(self): # Run at the start of each run_once(), only for things that are constand in seq

        # Do I need break_realtime here()? Does the caller not guarantuee this?
        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()

        self.device_setup_subfragments() # The different function name here it so satisfy the compiler
    
    # --- In seq action funcs ---

    @kernel
    def apply_dds_settings(self):
        """
        Changes the DDS parameters to a different freq/amp. Ideally the attenuator doesn't need to change
        mid sequence, but is allowed to. This function can be used alone to just change the beam freq/amp
        without changing the state of the RF switches.
        """
        self.dds_ch_rb_cool.set(self.cool_frequency.use(), amplitude=self.cool_dds_amp.use())
        self.dds_ch_rb_repump.set(self.repump_frequency.use(), amplitude=self.repump_dds_amp.use())
        
        # Changing the attenuators is a more costly effect
        if self.cool_dds_att.changed_after_use():
            self.dds_ch_rb_cool.set_att(self.cool_dds_att.use())
        if self.repump_dds_att.changed_after_use():
            self.dds_ch_rb_repump.set_att(self.repump_dds_att.use())


    @kernel
    def turn_light_on_now(self, pre_open_shutters=True):
        """
        Turn the cool+repump beams on `now`. This function will open the requisite shutters (if asked)
        and RF switches. It writes shutter events in the past, so be sure to have enough slack for this.
        """
        if pre_open_shutters:
            # Write shutter open into the past.
            # You must have sufficient slack for this to work.
            delay(-10*ms) # This doesn't actually wait, just moves the cursor
            self.ttl_rb_cool_shut.on()
            self.ttl_rb_repump_shut.on()
            delay(10*ms)
            # cursor is now back where this was called.
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

class SetShims(Fragment):
    """
    Set the shim fields to the given setpoints. This currently uses the DAC to send voltages to
    the control drivers.
    """

    def build_fragment(self):
        self.setattr_param("NS_setpoint",
                           FloatParam,
                           "N/S Shims servo setpoint voltage",
                           0.0*V,
                           min=-10*V, max=+10*V)
        self.NS_setpoint: FloatParamHandle

        self.setattr_param("EW_setpoint",
                           FloatParam,
                           "E/W Shims servo setpoint voltage",
                           0.0*V,
                           min=-10*V, max=+10*V)
        self.EW_setpoint: FloatParamHandle

        self.setattr_param("UD_setpoint",
                           FloatParam,
                           "U/D Shims servo setpoint voltage",
                           0.0*V,
                           min=-10*V, max=+10*V)
        self.UD_setpoint: FloatParamHandle
        
        self.setattr_device("core")
        self.core: Core
        self.setattr_device("zotino0")
        self.zotino0: Zotino

    @kernel
    def rtio_init_once(self):
        # If some host logic was just run, we need to make sure the timeline is valid
        self.core.break_realtime()
        # Initialise
        self.zotino0.init() # Make sure the Zotino exists. Don't bother nulling it, because we're about to set it.
    
    def host_setup(self): # Run once at the start of exp alongside all other host_setups, recursively.
        self.rtio_init_once()
         # To continue to initialise all subfragments, invoke the parent implementation:
        super().host_setup()

    
    def device_setup(self): # Run at the start of each run_once()
        # Do I need break_realtime here()? Does the caller not guarantuee this?
        self.device_setup_subfragments() # The different function name here it so satisfy the compiler

    # -- In Seq action functions --
    @kernel
    def set_shims(self):
        self.zotino0.set_dac(
            [self.NS_setpoint.use(), self.EW_setpoint.use(), self.UD_setpoint.use()],
            [0, 1, 2],
        )

class ShimRamp(Fragment):
    "Linearly ramp shim setpoints between two values"

    def build_fragment(self):
        self.setattr_param("NS_start_setpoint",
                           FloatParam,
                           "N/S Shims servo setpoint voltage at start of ramp",
                           0.0*V,
                           min=-10*V, max=+10*V)
        self.NS_start_setpoint: FloatParamHandle

        self.setattr_param("NS_end_setpoint",
                           FloatParam,
                           "N/S Shims servo setpoint voltage at end of ramp",
                           0.0*V,
                           min=-10*V, max=+10*V)
        self.NS_end_setpoint: FloatParamHandle

        self.setattr_param("EW_start_setpoint",
                           FloatParam,
                           "E/W Shims servo setpoint voltage at start of ramp",
                           0.0*V,
                           min=-10*V, max=+10*V)
        self.EW_start_setpoint: FloatParamHandle

        self.setattr_param("EW_end_setpoint",
                           FloatParam,
                           "E/W Shims servo setpoint voltage at end of ramp",
                           0.0*V,
                           min=-10*V, max=+10*V)
        self.EW_end_setpoint: FloatParamHandle

        self.setattr_param("UD_start_setpoint",
                           FloatParam,
                           "U/D Shims servo setpoint voltage at start of ramp",
                           0.0*V,
                           min=-10*V, max=+10*V)
        self.UD_start_setpoint: FloatParamHandle

        self.setattr_param("UD_end_setpoint",
                           FloatParam,
                           "U/D Shims servo setpoint voltage at end of ramp",
                           0.0*V,
                           min=-10*V, max=+10*V)
        self.UD_end_setpoint: FloatParamHandle
        
        self.setattr_param("ramp_time",
                           FloatParam,
                           "Ramp time, note the end value will be set at exactly ramp_time after (final dig step length jitter).",
                           min=0*us, max=100*ms
                           )
        self.ramp_time: FloatParamHandle
        
        self.setattr_param("step_period",
                           FloatParam,
                           "How long each step of the ADC takes. 1/step_period is the update frequency.",
                           min=1*us, max=100*ms
                           )
        self.step_period: FloatParamHandle
        
        self.setattr_device("core")
        self.core: Core
        self.setattr_device("zotino0")
        self.zotino0: Zotino
    

    @kernel
    def rtio_init_once(self):
        # If some host logic was just run, we need to make sure the timeline is valid
        self.core.break_realtime()
        # Initialise
        self.zotino0.init() # Make sure the Zotino exists. Don't bother nulling it, because we're about to set it.
    
    def host_setup(self): # Run once at the start of exp alongside all other host_setups, recursively.
        self.rtio_init_once()
         # To continue to initialise all subfragments, invoke the parent implementation:
        super().host_setup()

    @kernel
    def ramp_shims(self):

        ramp_time = self.ramp_time.get()
        step_period = self.step_period.get()

        NS0 = self.NS_start_setpoint.get()
        NS1 = self.NS_end_setpoint.get()
        EW0 = self.EW_start_setpoint.get()
        EW1 = self.EW_end_setpoint.get()
        UD0 = self.UD_start_setpoint.get()
        UD1 = self.UD_end_setpoint.get()

        # Degenerate cases
        if ramp_time <= 0*us:
            self.zotino0.set_dac([NS1, EW1, UD1], [0, 1, 2])
            return

        # Linear slope in V/s (units are fine as long as ramp_time is in seconds)
        sNS = (NS1 - NS0) / ramp_time
        sEW = (EW1 - EW0) / ramp_time
        sUD = (UD1 - UD0) / ramp_time

        n_full = int(ramp_time / step_period)   # number of full step_period intervals
        residual = ramp_time - n_full*step_period

        # t = 0
        t = 0.0*s
        self.zotino0.set_dac([NS0, EW0, UD0], [0, 1, 2])

        # t = i*step_period for i=1..n_full
        for _ in range(n_full):
            delay(step_period)
            t += step_period
            self.zotino0.set_dac([NS0 + sNS*t, EW0 + sEW*t, UD0 + sUD*t], [0, 1, 2])

        # Final clamp at exactly ramp_time
        if residual > 0*us:
            delay(residual)
        self.zotino0.set_dac([NS1, EW1, UD1], [0, 1, 2])



class QuadUse(Fragment):
    def build_fragment(self):
        self.setattr_param("quad_setpoint",
                           FloatParam,
                           "Quad setpoint",
                           8*V,
                           min=0*V,max=10*V
                           )
        self.quad_setpoint:FloatParamHandle
    
        self.setattr_device("core")
        self.core: Core
        self.setattr_device("zotino0")
        self.zotino0: Zotino
        self.setattr_device("ttl_quad")
        self.ttl_quad: TTLOut

    @kernel
    def turn_on(self):
        self.zotino0.set_dac([self.quad_setpoint.use()], [3])
        self.ttl_quad.on()
    
    @kernel
    def turn_off(self):
        self.ttl_quad.off()

class LoadRbMOT(Fragment):
    def build_fragment(self):
        self.setattr_fragment("MOT_set_shims", SetShims)
        self.setattr_fragment("MOT_quad", QuadUse)
        self.setattr_fragment("MOT_fluoresce", Fluoresce)

        self.setattr_device("core")

    @kernel
    def load_mot_on(self):
        self.MOT_set_shims.set_shims()
        self.MOT_quad.turn_on()
        self.MOT_fluoresce.turn_light_on_now(pre_open_shutters=True)

    @kernel
    def load_mot_off(self):
        self.MOT_quad.turn_off()
        self.MOT_fluoresce.turn_light_off_now(close_shutters=True)
    
class LoadRbMOTImage(ExpFragment):
    def build_fragment(self):
        self.setattr_fragment("Rb_MOT_loader", LoadRbMOT)
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
        self.setattr_result("mot_image", OpaqueChannel)

        # Devices
        self.setattr_device("core")
        self.setattr_device("andor_ctrl")
        self.setattr_device("ttl_camera_exposure")

    @kernel
    def rtio_events(self):
        self.core.break_realtime()
        delay(20*ms) # Add some slack for shutters to open
        
        self.Rb_MOT_loader.load_mot_on()
        delay(self.Rb_MOT_preload_time.use())
        self.ttl_camera_exposure.pulse(self.exposure_time.get())
        self.Rb_MOT_loader.load_mot_off()

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
    
    def run_once(self):
        self.andor_ctrl.start_acquisition()

        self.rtio_events()
        
        self.andor_ctrl.wait()
        img = self.andor_ctrl.get_image16()
        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()

        self.mot_image.push(img)
        self.set_dataset("andor.image", img, broadcast=True)


LoadRbMOTImageExp = make_fragment_scan_exp(LoadRbMOTImage)


class CompressMOT(Fragment):

    def build_fragment(self):
        self.setattr_fragment("shim_ramp_after_MOT", ShimRamp)

        
    def compress(self):
        self.shim_ramp_after_MOT.ramp_shims() # Shim it to where the tweezers are


class LoadMOTToTweezers(Fragment):

    def build_fragment(self):
        self.setattr_fragment("Rb_MOT_loader", LoadRbMOT)
        self.setattr_fragment("Rb_MOT_compressor", CompressMOT)
        self.setattr_fragment("Rb_molasses_shims", SetShims)
        
        self.setattr_param("Rb_MOT_load_time",
                    FloatParam,
                    100*ms,
                    min=1*ms,max=10*s)

        self.setattr_param("Rb_molasses_time",
                    FloatParam,
                    30*ms,
                    min=1*ms,max=10*s)
        
        self.setattr_param("tweezer_depth",
                           FloatParam,
                           8*V,
                           min=0*V, max=10*V
                           )


    def turn_tweezers_on():
        #TODO
        pass

    def load_mot_to_tweezers(self):
        self.core.break_realtime()
        delay(20*ms) # Add some slack for shutters to open
        
        self.Rb_MOT_loader.load_mot_on() # Load a mot
        self.turn_tweezers_on()
        delay(self.Rb_MOT_load_time.use())

        # Compress the MOT with increased Quad and temporal dark MOT
        # This step also shims the MOT to where the tweezers are.
        self.Rb_MOT_compressor.compress() 

        # Turn the Quad coils off, set shims to zero field, for molasses/PGRC.
        # self.Rb_MOT_loader.MOT_quad.turn_off()
        self.Rb_molasses_shims.set_shims()
        delay(Rb_molasses_time)

        self.Rb_MOT_loader.load_mot_off()

    

class LoadMOTToTweezersImage(ExpFragment):

    def build_fragment(self):
        self.setattr_fragment("load_mot_to_twe", LoadMOTToTweezers)
        # self.setattr_fragment("image_twe_after_mot", DualImageCool)
        self.setattr_device("ttl_camera_exposure")

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

    @kernel
    def rtio_events(self):
        self.load_mot_to_twe.load_mot_to_tweezers()
        # self.image_twe_after_mot.image()
        self.ttl_camera_exposure.pulse(self.exposure_time.get())

    def run_once(self):
        self.andor_ctrl.start_acquisition()

        self.rtio_events()
        
        self.andor_ctrl.wait()
        img = self.andor_ctrl.get_image16()
        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()

        self.mot_image.push(img)
        self.set_dataset("andor.image", img, broadcast=True)



LoadMOTToTweezersImageExp = make_fragment_scan_exp(LoadMOTToTweezersImage)