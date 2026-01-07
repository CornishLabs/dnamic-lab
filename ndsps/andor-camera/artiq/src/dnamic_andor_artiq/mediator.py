from artiq.language.core import kernel, delay
from artiq.language.units import us
import time


class AndorCamera:
    """
    ARTIQ-side mediator (local device).
    Combines the camera RPC driver interactions and the RTIO interactions over DIO
    under one interface.

    - RTIO side: deterministic TTL exposure window, shutter
    - Host side: RPC calls to the camera controller (sipyco pc_rpc proxy)
    """

    def __init__(self, dmgr, *, camera: str, exposure_ttl: str,
                 trigger_ttl: str | None = None, core: str = "core"):
        self.core = dmgr.get(core)
        self.cam = dmgr.get(camera)
        self.exposure_ttl = dmgr.get(exposure_ttl)

    @kernel
    def ttl_expose(self, t):
        """Drive the exposure TTL high for duration t (seconds)."""
        self.exposure_ttl.pulse(t)

    # -------------------------
    # Host (RPC) convenience API
    # -------------------------

    def arm(self):
        """
        Arm camera to wait for trigger/exposure.
        Prefer split steps if your controller exposes them.
        """
        if hasattr(self.cam, "prepare"):
            self.cam.prepare()
        if hasattr(self.cam, "start_acquisition"):
            self.cam.start_acquisition()
        else:
            raise RuntimeError("Controller missing start_acquisition()")

    def wait_done(self, timeout_s: float | None = None):
        """
        Wait for acquisition completion. If your controller has a timeout-capable wait,
        use it. Otherwise this will block indefinitely.
        """
        if timeout_s is None:
            return self.cam.wait()

        # Best: implement a timeout RPC in the controller.
        if hasattr(self.cam, "wait_timeout"):
            return self.cam.wait_timeout(timeout_s)

        # Fallback: naive host-side timeout loop if you have nonblocking status
        if hasattr(self.cam, "is_done"):
            t0 = time.time()
            while True:
                if self.cam.is_done():
                    return
                if time.time() - t0 > timeout_s:
                    raise TimeoutError("Camera wait timed out")
                time.sleep(0.005)

        raise RuntimeError("No timeout wait available (add wait_timeout() or is_done() to controller)")

    def fetch_image(self):
        """Fetch the image array via RPC."""
        if hasattr(self.cam, "get_image16"):
            return self.cam.get_image16()
        if hasattr(self.cam, "get_image"):
            return self.cam.get_image()
        raise RuntimeError("Controller missing get_image16()/get_image()")

    def acquire_with_ttl_exposure(self, exposure_s: float,
                                  *,
                                  trigger_pulse: bool = False,
                                  wait_timeout_s: float | None = None):
        """
        Typical pattern:
          host: arm camera
          kernel: perform TTL-defined exposure window
          host: wait and fetch image
        """
        # Ensure RTIO timeline is in a sane state before kernel actions
        self.core.break_realtime()

        self.arm()

        # Deterministic exposure timing on RTIO
        self.ttl_expose(exposure_s)

        # Back on host: wait for readout + fetch frame
        self.wait_done(timeout_s=wait_timeout_s)
        return self.fetch_image()
