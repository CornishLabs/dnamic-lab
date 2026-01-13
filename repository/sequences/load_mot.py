from ndscan.experiment import *
from contextlib import suppress

class LoadRbMOT(ExpFragment):
    def build_fragment(self):
        # Knobs
        self.setattr_param("cool_frequency",
                           FloatParam,
                           "Cool light AOM drive frequency",
                           110*MHz,
                           min=(110-50)*MHz, max=(110+50)*MHz)

        self.setattr_param("repump_frequency",
                           FloatParam,
                           "Repump light AOM drive frequency",
                           110*MHz,
                           min=(110-50)*MHz, max=(110+50)*MHz)

        self.setattr_param("cool_dds_amp",
                           FloatParam,
                           "Cool light AOM DDS amp (0-1)",
                           0.6,
                           min=0, max=1)

        self.setattr_param("repump_dds_amp",
                           FloatParam,
                           "Repump light AOM DDS amp (0-1)",
                           0.6,
                           min=0, max=1)

        self.setattr_param("cool_dds_att",
                           FloatParam,
                           "Cool light AOM DDS attenuator",
                           3.0*dB,
                           min=0*dB, max=30*dB)

        self.setattr_param("repump_dds_att",
                           FloatParam,
                           "Repump light AOM DDS attenuator",
                           3.0*dB,
                           min=0*dB, max=30*dB)

        self.setattr_param("quad_setpoint",
                           FloatParam,
                           "Quad coil servo setpoint voltage",
                           8.8*V,
                           min=0*V, max=10*V)

        self.setattr_param("NS_setpoint",
                           FloatParam,
                           "N/S Shims servo setpoint voltage",
                           "dataset('calib.mot_ns_shims', 0.8*V)",
                           min=-10*V, max=+10*V)

        self.setattr_param("EW_setpoint",
                           FloatParam,
                           "E/W Shims servo setpoint voltage",
                           "dataset('calib.mot_ew_shims', -0.367*V)",
                           min=-10*V, max=+10*V)

        self.setattr_param("UD_setpoint",
                           FloatParam,
                           "U/D Shims servo setpoint voltage",
                           "dataset('calib.mot_ud_shims', -0.119*V)",
                           min=-10*V, max=+10*V)

        self.setattr_param("preload_time",
                           FloatParam,
                           "Time to load MOT before imaging starts",
                           3*s,
                           min=1*ms, max=30*s)

        self.setattr_param("exposure_time",
                           FloatParam,
                           "Time spent fluorescing while exposing",
                           1*s,
                           min=1*ms, max=30*s)

        # Results
        self.setattr_result("mot_image", OpaqueChannel)

        # Devices
        self.setattr_device("core")
        self.setattr_device("andor_ctrl")
        self.setattr_device("ttl_camera_exposure")

        self.setattr_device("dds_ch_rb_cool")
        self.setattr_device("dds_ch_rb_repump")
        self.setattr_device("dds_cpld_rb")

        self.setattr_device("zotino0")

    def host_setup(self):
        super().host_setup()
        self._configure_camera()
        self._rt_init()
        print(self.__dir__())

    def _configure_camera(self):
        ROI = (0, 511, 0, 511)  # x0, x1, y0, y1 (inclusive)

        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()

        self.andor_ctrl.set_shutter(mode=5)
        self.andor_ctrl.set_trigger_mode(7)   # external exposure
        self.andor_ctrl.set_image_region(*ROI)

    @kernel
    def _rt_init(self):
        self.core.reset()
        self.core.break_realtime()

        self.dds_cpld_rb.init()
        self.dds_ch_rb_cool.init()
        self.dds_ch_rb_repump.init()
        self.zotino0.init()

        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.zotino0.set_dac([0.0*V], [3])

    @kernel
    def device_setup(self):
        self.device_setup_subfragments()
        self.core.break_realtime()

        if self.cool_frequency.changed_after_use() or self.cool_dds_amp.changed_after_use():
            self.dds_ch_rb_cool.set(self.cool_frequency.use(),
                                    amplitude=self.cool_dds_amp.use())
        if self.cool_dds_att.changed_after_use():
            self.dds_ch_rb_cool.set_att(self.cool_dds_att.use())

        if self.repump_frequency.changed_after_use() or self.repump_dds_amp.changed_after_use():
            self.dds_ch_rb_repump.set(self.repump_frequency.use(),
                                      amplitude=self.repump_dds_amp.use())
        if self.repump_dds_att.changed_after_use():
            self.dds_ch_rb_repump.set_att(self.repump_dds_att.use())

        self.zotino0.set_dac(
            [self.NS_setpoint.use(), self.EW_setpoint.use(),
             self.UD_setpoint.use(), self.quad_setpoint.use()],
            [0, 1, 2, 3],
        )

        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()

    @kernel
    def rt_actions(self):
        self.core.break_realtime()

        self.dds_ch_rb_cool.sw.on()
        self.dds_ch_rb_repump.sw.on()

        delay(self.preload_time.get())
        self.ttl_camera_exposure.pulse(self.exposure_time.get())

        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.zotino0.set_dac([0.0*V], [3])

    @kernel
    def device_cleanup(self):
        self.core.break_realtime()
        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.zotino0.set_dac([0.0*V], [3])
        self.device_cleanup_subfragments()

    def run_once(self):
        self.andor_ctrl.start_acquisition()

        self.rt_actions()

        self.andor_ctrl.wait()
        img = self.andor_ctrl.get_image16()
        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()

        self.mot_image.push(img)
        self.set_dataset("andor.image", img, broadcast=True)


MOTLoadExp = make_fragment_scan_exp(LoadRbMOT)
