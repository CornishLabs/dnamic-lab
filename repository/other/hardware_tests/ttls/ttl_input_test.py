from artiq.experiment import *

class CopyRisingEdges(EnvExperiment):
    """
    Copies rising edges from `ttl_in` to short pulses on `ttl_out`.

    Wiring:
      - Connect your external signal to ttl_in (input mode).
      - Observe pulses on ttl_out (output mode).

    Tunables:
      - copy_offset: fixed delay after the detected edge before the output pulse.
      - pulse_width: width of the output pulse.
      - wait_timeout: how long to wait for the next edge each loop iteration.

    Notes:
      * Keep `copy_offset` a few microseconds to guarantee we're always
        scheduling into the future (avoids RTIOUnderflow).
      * If input edges can arrive faster than `pulse_width`, pulses may merge
        (RTIO event replacement).
    """

    def build(self):
        self.setattr_device("core")
        self.ttl_in  = self.get_device("ttl4")   # TTLInOut
        self.ttl_out = self.get_device("ttl0")   # TTLOut or TTLInOut

        self.setattr_argument("copy_offset", NumberValue(2*us, unit="s", step=100*ns, ndecimals=3))
        self.setattr_argument("pulse_width", NumberValue(200*ns, unit="s", step=10*ns, ndecimals=3))
        self.setattr_argument("wait_timeout", NumberValue(1*s, unit="s", step=1*ms, ndecimals=3))

        # Optional: stop after N edges (-1 = run forever)
        self.setattr_argument("max_edges", NumberValue(-1, min=-1, ndecimals = 0, scale = 1, step=1, type='int'))

    @kernel
    def run(self):
        self.core.reset()

        # Configure directions
        self.ttl_in.input()
        self.ttl_out.output()

        # Small settling delay
        delay(1*ms)

        # Flush any stale input events
        while self.ttl_in.timestamp_mu(now_mu()) >= 0:
            pass
        delay(5*us)  # a bit of slack before starting

        # Convert constants once in the kernel
        offset_mu = self.core.seconds_to_mu(self.copy_offset)
        width_mu  = self.core.seconds_to_mu(self.pulse_width)
        to_mu     = self.core.seconds_to_mu(self.wait_timeout)

        # Keep the input “gate” open for rising edges the whole time.
        # (_set_sensitivity(1) == gate rising)
        self.ttl_in._set_sensitivity(1)
        try:
            n = 0
            while (self.max_edges < 0) or (n < self.max_edges):
                # Wait for next rising edge (with a timeout so the loop stays responsive)
                t_deadline = now_mu() + to_mu
                t_edge = self.ttl_in.timestamp_mu(t_deadline)

                if t_edge < 0:
                    # Timeout: no edge yet; loop again
                    continue

                # Schedule the output pulse at a fixed offset after the input edge
                at_mu(t_edge + offset_mu)
                self.ttl_out.on()
                delay_mu(width_mu)
                self.ttl_out.off()

                n += 1
        finally:
            # Always turn sensitivity back off if we exit
            self.ttl_in._set_sensitivity(0)
