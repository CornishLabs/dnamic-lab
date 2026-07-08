from contextlib import suppress

from artiq.experiment import *


class LoadMOTVanilla(EnvExperiment):
    """Minimal ARTIQ-only MOT load and Andor image."""

    def build(self):
        self.setattr_device("core")
        self.setattr_device("andor_ctrl")

        self.setattr_device("ttl_camera_exposure")
        self.setattr_device("ttl_quad")
        self.setattr_device("ttl_rb_cool_shut")
        self.setattr_device("ttl_rb_repump_shut")

        self.setattr_device("dds_cpld_rb")
        self.setattr_device("dds_ch_rb_cool")
        self.setattr_device("dds_ch_rb_repump")

        self.setattr_device("zotino0")

    def configure_camera(self):
        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()

        self.andor_ctrl.set_shutter(mode=5) # 5=Open for series
        self.andor_ctrl.set_trigger_mode(7)  # external exposure
        self.andor_ctrl.set_image_region(0, 511, 0, 511)

    @kernel
    def rt_init(self):
        self.core.reset()
        self.core.break_realtime()

        self.ttl_camera_exposure.off()
        self.ttl_quad.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()

        self.dds_cpld_rb.init()
        self.dds_ch_rb_cool.init()
        self.dds_ch_rb_repump.init()

        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()

        self.core.break_realtime()
        self.zotino0.init()
        self.zotino0.set_dac([0.0 * V, 0.0 * V, 0.0 * V, 0.0 * V], [0, 1, 2, 3])

    @kernel
    def rt_load_and_expose(self):
        self.core.break_realtime()

        self.dds_ch_rb_cool.set(101.25 * MHz, amplitude=0.45)
        self.dds_ch_rb_repump.set(80.64 * MHz, amplitude=0.32)
        self.dds_ch_rb_cool.set_att(8.0 * dB)
        self.dds_ch_rb_repump.set_att(8.0 * dB)

        self.zotino0.set_dac(
            [-0.367 * V, 0.8 * V, -0.112 * V, 8.8 * V],
            [0, 1, 2, 3],
        )

        self.ttl_camera_exposure.off()
        self.ttl_rb_cool_shut.on()
        self.ttl_rb_repump_shut.on()
        self.ttl_quad.on()
        delay(20 * ms)

        self.dds_ch_rb_cool.sw.on()
        self.dds_ch_rb_repump.sw.on()

        delay(3 * s)
        self.ttl_camera_exposure.pulse(1 * s)

        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()
        self.ttl_quad.off()
        self.zotino0.set_dac([0.0 * V, 0.0 * V, 0.0 * V, 0.0 * V], [0, 1, 2, 3])

    @kernel
    def rt_cleanup(self):
        self.core.break_realtime()

        self.ttl_camera_exposure.off()
        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()
        self.ttl_quad.off()
        self.zotino0.set_dac([0.0 * V, 0.0 * V, 0.0 * V, 0.0 * V], [0, 1, 2, 3])

    def run(self):
        self.configure_camera()
        self.rt_init()

        try:
            self.andor_ctrl.start_acquisition()
            self.rt_load_and_expose()
            self.andor_ctrl.wait()
            img = self.andor_ctrl.get_image16()
        finally:
            with suppress(Exception):
                self.andor_ctrl.abort_acquisition()
            with suppress(Exception):
                self.rt_cleanup()

        self.set_dataset("andor.image", img, broadcast=True)
        print(f"Got image: shape={img.shape} dtype={img.dtype} bytes={img.nbytes}")
