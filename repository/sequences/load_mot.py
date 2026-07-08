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
from ndscan.define.result_channels import FloatChannel
from ndscan.define.result_channels import IntChannel
from ndscan.runtime.api import make_fragment_prepared_dashboard_scan_exp


class RbMOTHardwareInit(Fragment):
    """Initialise MOT core devices once for one experiment instance."""

    def build_fragment(self) -> None:
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

        self.setattr_device("dds_cpld_rb")
        self.dds_cpld_rb: CPLD
        self.setattr_device("dds_ch_rb_cool")
        self.dds_ch_rb_cool: AD9910
        self.setattr_device("dds_ch_rb_repump")
        self.dds_ch_rb_repump: AD9910

        self.setattr_device("zotino0")
        self.zotino0: Zotino

        self.first_run = True

    @kernel
    def device_setup(self) -> None:
        self.device_setup_subfragments()

        if not self.first_run:
            return
        self.first_run = False

        self.core.reset()
        self.core.break_realtime()
        delay(1 * ms)

        self.ttl_camera_exposure.off()
        self.ttl_quad.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()

        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()

        self.core.break_realtime()
        delay(20 * ms)
        self.dds_cpld_rb.init()
        self.core.break_realtime()
        delay(20 * ms)
        self.dds_ch_rb_cool.init()
        self.core.break_realtime()
        delay(20 * ms)
        self.dds_ch_rb_repump.init()

        self.core.break_realtime()
        self.zotino0.init()
        self.core.break_realtime()
        self.zotino0.set_dac(
            [0.0 * V, 0.0 * V, 0.0 * V, 0.0 * V],
            [0, 1, 2, 3],
        )


class LoadRbMOT(ExpFragment):
    def build_fragment(self) -> None:
        self.setattr_fragment("hardware_init", RbMOTHardwareInit)
        self.hardware_init: RbMOTHardwareInit

        self.setattr_param(
            "cool_frequency",
            FloatParam,
            "Cool light AOM drive frequency",
            101.25 * MHz,
            min=(110 - 50) * MHz,
            max=(110 + 50) * MHz,
        )
        self.cool_frequency: FloatParamHandle

        self.setattr_param(
            "repump_frequency",
            FloatParam,
            "Repump light AOM drive frequency",
            80.64 * MHz,
            min=(110 - 50) * MHz,
            max=(110 + 50) * MHz,
        )
        self.repump_frequency: FloatParamHandle

        self.setattr_param(
            "cool_dds_amp",
            FloatParam,
            "Cool light AOM DDS amp (0-1)",
            0.45,
            min=0,
            max=1,
        )
        self.cool_dds_amp: FloatParamHandle

        self.setattr_param(
            "repump_dds_amp",
            FloatParam,
            "Repump light AOM DDS amp (0-1)",
            0.32,
            min=0,
            max=1,
        )
        self.repump_dds_amp: FloatParamHandle

        self.setattr_param(
            "cool_dds_att",
            FloatParam,
            "Cool light AOM DDS attenuator",
            8.0 * dB,
            min=5.0 * dB,
            max=30 * dB,
        )
        self.cool_dds_att: FloatParamHandle

        self.setattr_param(
            "repump_dds_att",
            FloatParam,
            "Repump light AOM DDS attenuator",
            8.0 * dB,
            min=5.0 * dB,
            max=30 * dB,
        )
        self.repump_dds_att: FloatParamHandle

        self.setattr_param(
            "quad_setpoint",
            FloatParam,
            "Quad coil servo setpoint voltage",
            8.8 * V,
            min=0 * V,
            max=10 * V,
        )
        self.quad_setpoint: FloatParamHandle

        self.setattr_param(
            "EW_setpoint",
            FloatParam,
            "E/W Shims servo setpoint voltage",
            -0.367 * V,
            min=-10 * V,
            max=+10 * V,
        )
        self.EW_setpoint: FloatParamHandle

        self.setattr_param(
            "UD_setpoint",
            FloatParam,
            "U/D Shims servo setpoint voltage",
            0.8 * V,
            min=-10 * V,
            max=+10 * V,
        )
        self.UD_setpoint: FloatParamHandle

        self.setattr_param(
            "NS_setpoint",
            FloatParam,
            "N/S Shims servo setpoint voltage",
            -0.112 * V,
            min=-10 * V,
            max=+10 * V,
        )
        self.NS_setpoint: FloatParamHandle

        self.setattr_param(
            "preload_time",
            FloatParam,
            "Time to load MOT before imaging starts",
            3 * s,
            min=1 * ms,
            max=30 * s,
        )
        self.preload_time: FloatParamHandle

        self.setattr_param(
            "exposure_time",
            FloatParam,
            "Time spent fluorescing while exposing",
            1 * s,
            min=1 * ms,
            max=30 * s,
        )
        self.exposure_time: FloatParamHandle

        self.setattr_result(
            "mot_image",
            ArrayChannel,
            element_type="int",
            shape=(512, 512),
            dim_names=("y", "x"),
            min=0,
            max=65535,
        )
        self.setattr_result(
            "dds_setup_shot",
            IntChannel,
            "DDS setup shot counter",
            min=0,
            display_hints={"priority": 8},
        )
        self.setattr_result(
            "dds_setup_ok",
            IntChannel,
            "DDS setup completed",
            min=0,
            max=1,
            display_hints={"priority": 8},
        )
        self.setattr_result(
            "cool_dds_amp_applied",
            FloatChannel,
            "Cool DDS amplitude applied",
            min=0,
            max=1,
            display_hints={"priority": 10},
        )
        self.setattr_result(
            "repump_dds_amp_applied",
            FloatChannel,
            "Repump DDS amplitude applied",
            min=0,
            max=1,
            display_hints={"priority": 7},
        )
        self.setattr_result(
            "cool_frequency_applied",
            FloatChannel,
            "Cool DDS frequency applied",
            unit="MHz",
            display_hints={"priority": 6},
        )
        self.setattr_result(
            "total_fluorescence",
            FloatChannel,
            "Total fluorescence",
            min=0,
            unit="counts",
            scale=1.0,
            display_hints={"priority": 12},
        )

        self.setattr_device("core")
        self.core: Core

        self.setattr_device("andor_ctrl")

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

        self._first_device_setup = True
        self._setup_shot_index = 0

    def host_setup(self) -> None:
        super().host_setup()
        self.configure_camera()

    def host_cleanup(self) -> None:
        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()
        super().host_cleanup()

    def configure_camera(self) -> None:
        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()

        self.andor_ctrl.set_shutter(mode=5)
        self.andor_ctrl.set_trigger_mode(7)  # external exposure
        self.andor_ctrl.set_image_region(0, 511, 0, 511)

    @rpc
    def camera_start_acquisition(self):
        self.andor_ctrl.start_acquisition()

    @rpc
    def camera_abort_acquisition(self):
        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()

    @rpc
    def camera_wait_read_and_publish(self):
        self.andor_ctrl.wait()
        img = self.andor_ctrl.get_image16()
        total_fluorescence = float(img.sum())
        self.mot_image.push(img)
        self.total_fluorescence.push(total_fluorescence)
        self.set_dataset("andor.image", img, broadcast=True)
        self.set_dataset(
            "andor.total_fluorescence",
            total_fluorescence,
            broadcast=True,
        )

    @kernel
    def device_setup(self) -> None:
        self.device_setup_subfragments()
        self.core.break_realtime()
        delay(1 * ms)

        self._setup_shot_index += 1

        cool_frequency = self.cool_frequency.use()
        cool_dds_amp = self.cool_dds_amp.use()
        cool_dds_att = self.cool_dds_att.use()
        repump_frequency = self.repump_frequency.use()
        repump_dds_amp = self.repump_dds_amp.use()
        repump_dds_att = self.repump_dds_att.use()
        ew_setpoint = self.EW_setpoint.use()
        ud_setpoint = self.UD_setpoint.use()
        ns_setpoint = self.NS_setpoint.use()
        quad_setpoint = self.quad_setpoint.use()

        self.core.break_realtime()
        self.dds_ch_rb_cool.set(
            cool_frequency,
            amplitude=cool_dds_amp,
        )

        self.core.break_realtime()
        self.dds_ch_rb_cool.set_att(cool_dds_att)

        self.core.break_realtime()
        self.dds_ch_rb_repump.set(
            repump_frequency,
            amplitude=repump_dds_amp,
        )

        self.core.break_realtime()
        self.dds_ch_rb_repump.set_att(repump_dds_att)

        self.core.break_realtime()
        self.zotino0.set_dac(
            [
                ew_setpoint,
                ud_setpoint,
                ns_setpoint,
                quad_setpoint,
            ],
            [0, 1, 2, 3],
        )

        self.dds_setup_shot.push(self._setup_shot_index)
        self.dds_setup_ok.push(1)
        self.cool_dds_amp_applied.push(cool_dds_amp)
        self.repump_dds_amp_applied.push(repump_dds_amp)
        self.cool_frequency_applied.push(cool_frequency)

        self.core.break_realtime()
        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()
        self.ttl_camera_exposure.off()
        self.ttl_quad.off()
        self._first_device_setup = False

    @kernel
    def rt_actions(self) -> None:
        self.core.break_realtime()

        self.ttl_rb_cool_shut.on()
        self.ttl_rb_repump_shut.on()
        self.ttl_quad.on()
        delay(20 * ms)

        self.dds_ch_rb_cool.sw.on()
        self.dds_ch_rb_repump.sw.on()

        delay(self.preload_time.get())
        self.ttl_camera_exposure.pulse(self.exposure_time.get())

        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()
        self.ttl_quad.off()
        self.core.break_realtime()
        self.zotino0.set_dac([0.0 * V], [3])
        delay(20 * ms)

    @kernel
    def device_cleanup(self) -> None:
        self.core.break_realtime()
        self.ttl_camera_exposure.off()
        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()
        self.ttl_quad.off()
        self.core.break_realtime()
        self.zotino0.set_dac(
            [0.0 * V, 0.0 * V, 0.0 * V, 0.0 * V],
            [0, 1, 2, 3],
        )
        self.device_cleanup_subfragments()

    @kernel
    def run_once(self) -> None:
        self.camera_start_acquisition()
        self.core.break_realtime()
        try:
            self.rt_actions()
            self.camera_wait_read_and_publish()
            self.core.break_realtime()
        finally:
            self.camera_abort_acquisition()
            self.core.break_realtime()


# MOTLoadExp = make_fragment_prepared_dashboard_scan_exp(LoadRbMOT)

MOTLoadExp = make_fragment_prepared_dashboard_scan_exp(
    LoadRbMOT,
    max_rtio_underflow_retries=0,
)
