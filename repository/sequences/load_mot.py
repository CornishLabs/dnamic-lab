from ndscan.experiment import *

class LoadRbMOT(ExpFragment):
    def build_fragment(self):
        # Knobs
        self.setattr_param("cool_frequency",
                           FloatParam,
                           "Cool light AOM drive frequency",
                           110*MHz,
                            min=(110-50)*MHz,max=(110+50)*MHz)
        
        self.setattr_param("repump_frequency",
                           FloatParam,
                           "Repump light AOM drive frequency",
                           110*MHz,
                           min=(110-50)*MHz,max=(110+50)*MHz)
        
        self.setattr_param("cool_dds_amp",
                            FloatParam,
                            "Cool light AOM dds amp (0-1)",
                            0.6,
                            min=0,max=1)
        
        self.setattr_param("repump_dds_amp",
                            FloatParam,
                            "Repump light AOM dds amp (0-1)",
                            0.6,
                            min=0,max=1)
        
        self.setattr_param("cool_dds_att",
                            FloatParam,
                            "Cool light AOM dds attenuator 0.5db res",
                            3.0*dB,
                            min=0*dB,max=30*dB)
        
        self.setattr_param("repump_dds_att",
                            FloatParam,
                            "Repump light AOM dds amp 0.5db res",
                            3.0*dB,
                            min=0*dB,max=30*dB)
        
        self.setattr_param("quad_setpoint",
                           FloatParam,
                           "Quad coil servo setpoint voltage",
                           8*V,
                           min=0*V, max=10*V
                           )
        
        self.setattr_param("NS_setpoint",
                    FloatParam,
                    "N/S Shims servo setpoint voltage",
                    0*V,
                    min=-10*V, max=+10*V
                    )
        
        self.setattr_param("EW_setpoint",
                    FloatParam,
                    "E/W Shims servo setpoint voltage",
                        0*V,
                    min=-10*V, max=+10*V
                    )
        
        self.setattr_param("UD_setpoint",
                    FloatParam,
                    "U/D Shims servo setpoint voltage",
                    0*V,
                    min=-10*V, max=+10*V
                    )
        
        self.setattr_param("preload_time",
                           FloatParam,
                           "Time to load MOT before imaging starts",
                           3*s,
                           min=1*ms, max=30*s
                           )
        
        self.setattr_param("exposure_time",
                           FloatParam,
                           "Time spent fluorescing while exposing",
                           1*s,
                           min=1*ms, max=30*s
                           )

        # Results
        self.setattr_result("mot_image", OpaqueChannel)

        # Device drivers
        self.setattr_device("core")
        self.setattr_device("andor_ctrl")
        self.camera_trigger = self.get_device("ttl0")
        
        self.rb_cool_dds = self.get_device("urukul5_ch0")
        self.rb_repump_dds = self.get_device("urukul5_ch1")
        self.rb_dds_cpld = self.get_device("urukul5_cpld")

        self.setattr_device("zotino0")
    
    @kernel
    def prepare_ddss(self):

        delay(2*ms)
        # Initialise CPLDs on Urukuls (DDS cards)
        for cpld in [self.rb_dds_cpld]:
            cpld.init()
        
        # Initialise DDS Channels on Urukuls
        for dds in [self.rb_cool_dds, self.rb_repump_dds]:
            dds.init()

        for dds, freq, amp, att in [
            (self.rb_cool_dds, self.cool_frequency.get(), self.cool_dds_amp.get(), self.cool_dds_att.get()),
            (self.rb_repump_dds, self.repump_frequency.get(), self.repump_dds_amp.get(), self.repump_dds_att.get()),
            ]:
            dds.sw.off()
            dds.set(freq, amplitude=amp)
            dds.set_att(att)

    @rpc
    def initialise_camera(self):
        
        ROI = (0, 511, 0, 511)  # x0, x1, y0, y1 (0-based inclusive)
        
        # Abort any previous acquisiton
        try:
            self.andor_ctrl.abort_acquisition()
        except:
            pass

        # 0=Full Auto, 1= Perm open 2=Perm closed, 5=Open for any series
        self.andor_ctrl.set_shutter(mode=5) 

        # Configure camera for a single image
        self.andor_ctrl.set_trigger_mode(7) # 0=internal, 6 = External start, 7 = External exposure
        self.andor_ctrl.set_image_region(*ROI)
        self.andor_ctrl.start_acquisition()

    @kernel
    def rt_actions(self):
        self.core.break_realtime() 
        # Initialise DDSs freq, amp, att
        self.prepare_ddss()

        # Open shutters the calibrated amount of time beforehand (TODO)
        
        # Turn Quad+shims on
        self.zotino0.set_dac(
            [self.NS_setpoint.get(), self.EW_setpoint.get(), self.UD_setpoint.get(), self.quad_setpoint.get()],
              [0,1,2,3]
        )

        # Turn RF switches on to turn AOMs on
        for dds in [self.rb_cool_dds, self.rb_repump_dds]:
            dds.sw.on()

        # Hold output on for desired preload time
        delay(self.preload_time.get())

        # Pulse the exposure (adds to the timeline)
        self.camera_trigger.pulse(self.exposure_time.get())

        # Turn RF off
        for dds in [self.rb_cool_dds, self.rb_repump_dds]:
            dds.sw.off()

        # Turn Quad off (Leave shims so field null)
        self.zotino0.set_dac([0.0*V], [3])

    def run_once(self):
        self.core.reset()

        # Initialise camera
        self.initialise_camera()
        self.core.break_realtime() # Move timeline cursor to after camera init + margin

        self.rt_actions()

        # Wait for camera to get image
        self.andor_ctrl.wait()
        img = self.andor_ctrl.get_image16()

        self.andor_ctrl.abort_acquisition()

        # Put image into dataset as opaque.
        self.mot_image.push(img)
        self.set_dataset("andor.image", img, broadcast=True)    


MOTLoadExp = make_fragment_scan_exp(LoadRbMOT)