from artiq.experiment import *

class MediatedSimpleCameraTest(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("andor_ctrl")
        self.setattr_device("ttl0")

    def camera_setup(self):
        ROI = (0, 511, 0, 511)
        c = self.andor_ctrl
        try:
            c.abort_acquisition()
        except Exception:
            pass

        c.cooler_on()
        c.set_temperature(-50)

        c.set_shutter(mode=5, opening_ms=28, closing_ms=28)        # 1=permanently open, 5=Open for series
        c.set_frame_transfer_mode(0)
        # External Bulb only exists in non-FT mode. I think this only gives speedup in video
        c.set_trigger_mode(7)        # 1=external 7=External Bulm 
        c.set_fast_ext_trigger(1)    
        c.set_image_region(*ROI)
        # c.set_exposure_time(0.05)

    def camera_teardown(self):
        try:
            self.andor_ctrl.abort_acquisition()
        except Exception:
            pass

    @kernel
    def ttl_expose(self):
        self.core.break_realtime()
        self.ttl0.off()
        delay(200*ms)                  # small slack is often helpful
        self.ttl0.pulse(0.5*ms)
        delay(1*ms)
        # self.core.wait_until_mu(now_mu())
        # self.core.break_realtime()

    def run(self):
        self.core.reset()

        self.camera_setup()

        c = self.andor_ctrl
        self.ttl_expose()
        c.start_acquisition()

        # Deterministic exposure timing happens here

        # Now do readout on the host
        c.wait()
        
        # self.core.break_realtime()
        img = c.get_image16()
        self.camera_teardown()

        self.set_dataset("andor.image", img, broadcast=True)

