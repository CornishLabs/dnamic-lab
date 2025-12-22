from artiq.experiment import *
from artiq.language.types import TTuple, TFloat


# ---------------------------
# Helpers (pure python)
# ---------------------------

def volts_to_norm(v_volts: float, full_scale_volts: float) -> float:
    """Convert a physical voltage to SU-Servo 'full-scale units' (roughly [-1,1))."""
    return v_volts / full_scale_volts


# ======================================================================
# 1) OPEN-LOOP: ADC voltage -> DDS amplitude (no physical feedback needed)
# ======================================================================

class SUServo_OpenLoop_ADCToAmplitude(EnvExperiment):
    """Open-loop "amplitude modulation" using SU-Servo.

    Concept
    -------
    - Sampler ADC0 measures some voltage (e.g. a function generator).
    - SU-Servo uses a *P-only* controller so that y[n] is proportional to (x[n] + o).
    - y drives DDS amplitude (ASF scale factor), so the DDS amplitude follows the ADC.

    This is NOT a stabilizing feedback loop unless the ADC signal depends on the DDS
    output through your experiment hardware.

    Wiring suggestion
    -----------------
    - Connect a function generator to Sampler CH0.
    - Observe Urukul CH0 RF output amplitude on a spectrum analyzer / RF diode detector.
    """

    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_device("suservo0_ch0")

    def run(self):
        self.setup()
        adc_v, y = self.readback()
        print("Open-loop modulation result:")
        print("  ADC0 voltage [V] =", adc_v)
        print("  y (0..1)          =", y)

    @kernel
    def setup(self):
        # Reset core and timeline.
        self.core.reset()
        self.core.break_realtime()

        # Initialize SU-Servo (Sampler + Urukuls). Leaves servo disabled.
        self.suservo0.init()

        # Set PGIA gain: gain=0 => 1x (typical full scale ~ +/-10 V).
        self.suservo0.set_pgia_mu(channel=0, gain=0)

        # Coarse RF attenuation (step attenuator). Use safe value for your setup.
        self.suservo0.cplds[0].set_att(0, 10.0)

        # Ensure channel is off while programming.
        self.suservo0_ch0.set(en_out=0, en_iir=0, profile=0)

        # --- Choose how ADC maps to amplitude ---
        #
        # In P-only mode (ki=0), y roughly follows kp*(x + offset), clipped to [0,1).
        #
        # A nice easy mapping is approximately:
        #   y ~ (x + 1)/2   (maps x=-1 -> 0, x=+1 -> 1)
        #
        # We approximate this using:
        #   kp = +0.5
        #   offset ~ +0.999   (can't represent exactly +1.0 in the coefficient format)
        #
        # This makes:
        #   y ~ 0.5*(x + 0.999)
        # so x=-1 gives a small negative value that clips to 0,
        # and x=+1 gives ~0.9995.
        adc_ch = 0
        kp = +0.5
        self.suservo0_ch0.set_iir(profile=0, adc=adc_ch, kp=kp, ki=0.0, g=0.0, delay=0.0)

        # DDS frequency & offset live in the SU-Servo profile memory.
        # Here "offset" is the IIR offset (negative setpoint) in full-scale units.
        # We are abusing it as a bias term for open-loop modulation.
        self.suservo0_ch0.set_dds(profile=0, frequency=10*MHz, offset=+0.999, phase=0.0)

        # Start from a known y (optional; P-only will update it anyway each cycle).
        self.suservo0_ch0.set_y(profile=0, y=0.0)

        # Turn RF on and enable IIR updates for this channel/profile.
        self.suservo0_ch0.set(en_out=1, en_iir=1, profile=0)

        # Enable the global SU-Servo engine (starts ADC sampling and DDS updates).
        self.suservo0.set_config(enable=1)

        # Let it run a bit.
        delay(20*ms)

        # Stop servo for safe readback.
        self.suservo0.set_config(enable=0)
        delay(5*us)

    @kernel
    def readback(self) -> TTuple([TFloat, TFloat]):
        # With the servo disabled and drained, reads are much less likely to collide.
        adc_v = self.suservo0.get_adc(0)
        y = self.suservo0_ch0.get_y(0)
        return adc_v, y


# ==========================================================================
# 2) CLOSED-LOOP: constant intensity lock (photodiode signal depends on DDS)
# ==========================================================================

class SUServo_ClosedLoop_ConstantIntensity(EnvExperiment):
    """Closed-loop intensity stabilization using SU-Servo.

    Concept
    -------
    - Urukul CH0 drives an AOM that sets laser intensity.
    - A photodiode measures the resulting intensity and outputs a voltage.
    - Sampler ADC0 measures that voltage => x.
    - SU-Servo controller updates y (DDS amplitude scale) to keep x near a setpoint.

    The setpoint is set by the "offset" parameter:
        offset = -x_set   (in normalized full-scale units)

    Notes on signs
    --------------
    If increasing DDS amplitude increases the photodiode voltage:
      - error ~= x - x_set
      - you typically want negative kp/ki so that when x > x_set, y is driven down.
    """

    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_device("suservo0_ch0")

    def run(self):
        self.lock()
        adc_v, y = self.readback()
        print("Closed-loop lock snapshot:")
        print("  ADC0 voltage [V] =", adc_v)
        print("  y (0..1)          =", y)

    @kernel
    def lock(self):
        self.core.reset()
        self.core.break_realtime()

        self.suservo0.init()

        # Gain settings: start with unity.
        self.suservo0.set_pgia_mu(channel=0, gain=0)

        # Coarse attenuation: choose safe for your AOM/driver chain.
        self.suservo0.cplds[0].set_att(0, 0.0)

        # Disable output while programming
        self.suservo0_ch0.set(en_out=0, en_iir=0, profile=0)

        # --- Setpoint ---
        # With PGIA gain=1, assume full-scale roughly 10 V.
        # Choose your desired photodiode voltage setpoint.
        full_scale = 10.0
        v_set = 3.0
        x_set = volts_to_norm(v_set, full_scale)
        offset = -x_set  # negative setpoint (so x+offset ~ x - x_set)

        # --- Controller gains ---
        # Start gently. A safe bring-up sequence is:
        #   1) P-only with small |kp|, verify sign and basic behaviour
        #   2) add integrator ki slowly
        #
        # These are "starter" numbers; you will likely retune for your AOM + detector.
        adc_ch = 0
        kp = -0.2         # proportional gain (dimensionless)
        ki = -2e4         # rad/s (integrator gain); start small
        g  = 0.3          # integrator limit (dimensionless); helps avoid wind-up
        self.suservo0_ch0.set_iir(profile=0, adc=adc_ch, kp=kp, ki=ki, g=g, delay=0.0)

        # Program DDS parameters. Frequency depends on your AOM (e.g. 80 MHz).
        self.suservo0_ch0.set_dds(profile=0, frequency=80*MHz, offset=offset, phase=0.0)

        # Initialize y to something moderate so you don't slam the AOM on enable.
        self.suservo0_ch0.set_y(profile=0, y=0.3)

        # Enable channel output and IIR updates.
        self.suservo0_ch0.set(en_out=1, en_iir=1, profile=0)

        # Enable global engine.
        self.suservo0.set_config(enable=1)

        # Let it settle.
        delay(50*ms)

        # Stop servo before reading.
        self.suservo0.set_config(enable=0)
        delay(5*us)

    @kernel
    def readback(self) -> TTuple([TFloat, TFloat]):
        adc_v = self.suservo0.get_adc(0)
        y = self.suservo0_ch0.get_y(0)
        return adc_v, y


# ==========================================================================
# 3) CLOSED-LOOP WITH RAMPS: step the setpoint by updating set_dds_offset()
# ==========================================================================

class SUServo_ClosedLoop_RampSetpoint(EnvExperiment):
    """Closed-loop control while ramping the setpoint.

    Concept
    -------
    - Keep the same closed-loop plant (AOM + photodiode).
    - Ramp the desired photodiode voltage in time by changing offset:
          offset(t) = -x_set(t)

    Implementation detail
    ---------------------
    - Updating offset is done by writing servo profile memory, i.e. RTIO events.
    - Very fast updates for long ramps can hit sustained event-rate limits.
      Practical ramps usually update at 1–50 kHz, or use coarse steps.
    """

    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_device("suservo0_ch0")

    def run(self):
        self.ramp_lock()
        adc_v, y = self.readback()
        print("After ramp:")
        print("  ADC0 voltage [V] =", adc_v)
        print("  y (0..1)          =", y)

    @kernel
    def ramp_lock(self):
        self.core.reset()
        self.core.break_realtime()

        self.suservo0.init()
        self.suservo0.set_pgia_mu(channel=0, gain=0)
        self.suservo0.cplds[0].set_att(0, 0.0)

        self.suservo0_ch0.set(en_out=0, en_iir=0, profile=0)

        # Controller: same caution as before; tune on your hardware.
        adc_ch = 0
        kp = -0.2
        ki = -2e4
        g  = 0.3
        self.suservo0_ch0.set_iir(profile=0, adc=adc_ch, kp=kp, ki=ki, g=g, delay=0.0)

        # DDS frequency fixed; setpoint will be ramped by offset changes.
        self.suservo0_ch0.set_dds(profile=0, frequency=80*MHz, offset=-0.1, phase=0.0)
        self.suservo0_ch0.set_y(profile=0, y=0.2)
        self.suservo0_ch0.set(en_out=1, en_iir=1, profile=0)

        # Enable global servo engine.
        self.suservo0.set_config(enable=1)

        # --- Ramp definition ---
        full_scale = 10.0
        v_start = 1.0
        v_end   = 4.0

        ramp_time = 20*ms
        step_period = 50*us       # 20 kHz update would be 50 us; here that's fine
        steps = int(ramp_time/step_period)
        if steps < 1:
            steps = 1

        # Perform ramp by stepping the setpoint.
        # set_dds_offset() expects the "offset" in full-scale units (negative setpoint).
        for i in range(steps + 1):
            frac = i / steps
            v_set = v_start + frac*(v_end - v_start)
            x_set = v_set / full_scale
            offset = -x_set
            self.suservo0_ch0.set_dds_offset(profile=0, offset=offset)
            delay(step_period)

        # Hold at final setpoint for a bit
        delay(20*ms)

        # Disable servo before readback.
        self.suservo0.set_config(enable=0)
        delay(5*us)

    @kernel
    def readback(self) -> TTuple([TFloat, TFloat]):
        adc_v = self.suservo0.get_adc(0)
        y = self.suservo0_ch0.get_y(0)
        return adc_v, y
