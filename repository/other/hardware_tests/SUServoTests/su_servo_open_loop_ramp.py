from artiq.experiment import *


class SUServoOpenLoopSetpointRamp(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_device("suservo0_ch0")

    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()

        self.suservo0.init()
        self.suservo0.set_pgia_mu(0, 0)  # unity gain
        self.suservo0.cplds[0].set_att(0, 15.)
        self.suservo0_ch0.set_y(profile=0, y=0.)  # clear integrator

        # P-only: y ~ -kp*(x - x_set). With kp=-1 => y ~ x_set - x
        self.suservo0_ch0.set_iir(
            profile=0,
            adc=0,
            kp=-1.0,
            ki=0.0/s,
            g=0.0,
            delay=0.0
        )

        # DDS base config (we’ll ramp setpoint via offset)
        self.suservo0_ch0.set_dds(
            profile=0,
            offset=-0.01,         # x_set = +0.1 initially
            frequency=10*MHz,
            phase=0.0
        )

        # Enable channel + IIR, then enable global engine
        self.suservo0_ch0.set(en_out=1, en_iir=1, profile=0)
        self.suservo0.set_config(enable=1)

        # --------------------------
        # Ramp setpoint by offset(t)
        # --------------------------
        full_scale_volts = 10.0
        v_start = 0.0
        v_end   = 5.0

        ramp_time   = 20*ms
        step_period = 2*us
        steps = int(ramp_time/step_period)
        if steps < 1:
            steps = 1

        for i in range(steps + 1):
            frac = i / steps
            v_set = v_start + frac*(v_end - v_start)
            x_set = v_set / full_scale_volts
            self.suservo0_ch0.set_dds_offset(profile=0, offset=-x_set)
            delay(step_period)

        # --------------------------
        # Make it go to zero at end
        # --------------------------
        # Freeze the integrator so it stops recomputing y from ADC.
        self.suservo0_ch0.set(en_out=1, en_iir=0, profile=0)

        # Force amplitude scale to zero (this creates your clean falling edge).
        self.suservo0_ch0.set_y(profile=0, y=0.0)

        # Give it a couple servo cycles to propagate to the DDS.
        delay(5*us)

        # Optional: also hard-disable RF switch for an unmistakable "off".
        # (Useful if anything still leaks through at y=0 depending on your chain.)
        self.suservo0_ch0.set(en_out=0, en_iir=0, profile=0)

        # Now you can disable the global engine if desired.
        self.suservo0.set_config(enable=0)
        delay(5*us)
