import time
from contextlib import suppress

from artiq.experiment import *
from artiq.master.scheduler import Scheduler


class AndorVideo(EnvExperiment):
    """Repeated single-frame Andor acquisition for live dataset display."""

    def build(self):
        self.setattr_device("andor_ctrl")
        self.setattr_device("scheduler")
        self.scheduler: Scheduler

        self.setattr_argument(
            "exposure_time",
            NumberValue(50 * ms, unit="ms", scale=ms, step=10 * ms, min=1 * ms),
        )
        self.setattr_argument(
            "frame_rate",
            NumberValue(2.0, unit="Hz", step=0.5, min=0.01, max=20.0),
        )
        self.setattr_argument(
            "max_frames",
            NumberValue(0, step=1, min=0, precision=0, type="int"),
        )
        self.setattr_argument("shutter_closed", BooleanValue(False))
        self.setattr_argument("x0", NumberValue(0, step=1, min=0, precision=0, type="int"))
        self.setattr_argument("x1", NumberValue(511, step=1, min=0, precision=0, type="int"))
        self.setattr_argument("y0", NumberValue(0, step=1, min=0, precision=0, type="int"))
        self.setattr_argument("y1", NumberValue(511, step=1, min=0, precision=0, type="int"))

    def prepare(self):
        self.frame_period = 1.0 / float(self.frame_rate)
        self.num_frames = int(self.max_frames)
        self.roi = (
            int(self.x0),
            int(self.x1),
            int(self.y0),
            int(self.y1),
        )

    def configure_camera(self):
        with suppress(Exception):
            self.andor_ctrl.abort_acquisition()

        self.andor_ctrl.cooler_on()
        self.andor_ctrl.set_temperature(-50)
        self.andor_ctrl.set_shutter(mode=2 if self.shutter_closed else 1)
        self.andor_ctrl.set_frame_transfer_mode(0)
        self.andor_ctrl.set_trigger_mode(0)  # internal trigger
        self.andor_ctrl.set_image_region(*self.roi)
        self.andor_ctrl.set_exposure_time(float(self.exposure_time))

    def acquire_frame(self):
        self.andor_ctrl.start_acquisition()
        self.andor_ctrl.wait()
        return self.andor_ctrl.get_image16()

    def run(self):
        self.configure_camera()
        self.set_dataset("andor.video_frame", 0, broadcast=True)

        frame = 0
        try:
            while self.num_frames == 0 or frame < self.num_frames:
                if self.scheduler.check_pause():
                    break

                t0 = time.monotonic()
                img = self.acquire_frame()
                frame += 1

                self.set_dataset("andor.image", img, broadcast=True)
                self.set_dataset("andor.video_frame", frame, broadcast=True)

                dt = time.monotonic() - t0
                sleep_s = self.frame_period - dt
                if sleep_s > 0:
                    time.sleep(sleep_s)
        finally:
            with suppress(Exception):
                self.andor_ctrl.abort_acquisition()
            with suppress(Exception):
                self.andor_ctrl.set_shutter(mode=2)  # permanently closed
