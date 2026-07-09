from artiq.language.core import kernel


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

    def arm(self, configure: bool = True):
        """Configure if requested, then arm for one externally timed image."""
        self.cam.start_external_exposure_single(configure=configure)

    def wait_done(self, timeout_s: float | None = None):
        timeout_ms = None if timeout_s is None else int(1000.0 * timeout_s)
        self.cam.wait(timeout_ms=timeout_ms)

    def fetch_image(self):
        """Fetch the image array via RPC."""
        return self.cam.get_image16()

    def acquire_with_ttl_exposure(self, exposure_s: float,
                                  *,
                                  trigger_pulse: bool = False,
                                  wait_timeout_s: float | None = None,
                                  configure: bool = True):
        """
        Typical pattern:
          host: arm camera
          kernel: perform TTL-defined exposure window
          host: wait and fetch image
        """
        # Ensure RTIO timeline is in a sane state before kernel actions
        self.arm(configure=configure)

        self.core.break_realtime()
        # Deterministic exposure timing on RTIO
        self.ttl_expose(exposure_s)

        timeout_ms = None if wait_timeout_s is None else int(1000.0 * wait_timeout_s)
        return self.cam.wait_get_image16(timeout_ms=timeout_ms)
