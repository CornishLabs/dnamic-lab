from artiq.experiment import *
import numpy as np


class ZotinoSawtooth_StepLocked_FinalPush_Satelite(EnvExperiment):
    """
    Generate a 0..AMP_V sawtooth on zotino0 and sample it on sampler2,
    with a fixed number of ADC samples per DAC step. Push the dataset once at the end.

    - DAC: SAW_FREQ_HZ with DAC_POINTS steps/period on DAC_CHANNEL.
    - ADC: SAMPLES_PER_STEP samples per DAC step on ADC_CHANNEL, starting after ADC_OFFSET_US.
    """

    def build(self):
        self.setattr_device("core")
        self.setattr_device("zotino0")
        self.setattr_device("sampler2")

        # ---- User parameters (keep it tight & familiar) ----
        self.SAW_FREQ_HZ        = 100.0     # Hz (sawtooth frequency)
        self.DAC_POINTS         = 100      # steps per period (resolution)
        self.DAC_CHANNEL        = 0

        self.SAMPLES_PER_STEP   = 4        # ADC samples taken per DAC step
        self.ADC_CHANNEL        = 0        # 0..7, zero top left, 7 bottom right
        self.ADC_OFFSET_US      = 5.0      # µs after the DAC update to take the first sample

        self.DURATION_S         = 0.02      # total acquisition time
        self.AMP_V              = 5.0      # 0..5 V if OFFSET_V=0
        self.OFFSET_V           = 0.0

        self.GUARD_US           = 100.0    # one-time initial slack (µs)

    def prepare(self):
        # One-period LUT for the sawtooth: [0..AMP_V) then wraps
        base = np.linspace(0.0, 1.0, self.DAC_POINTS, endpoint=False, dtype=np.float64)
        self.voltages = (self.AMP_V * base + self.OFFSET_V).astype(np.float64)

        # Helpful derived quantities
        self.dac_update_rate = self.SAW_FREQ_HZ * self.DAC_POINTS   # DAC sets per second

        # Number of DAC steps to cover the duration, and total ADC samples
        n_steps = int(np.ceil(self.DURATION_S * self.dac_update_rate))
        self.n_steps = max(1, n_steps)
        self.n_samples = self.n_steps * self.SAMPLES_PER_STEP

    # Host-side: final push of the full dataset at the end
    @rpc
    def _push_final(self, data: list):
        self.set_dataset("samples", np.array(data, dtype=np.float64), broadcast=True)

    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()

        # Init hardware
        self.zotino0.init()
        self.sampler2.init()
        for ch in range(8):
            self.sampler2.set_gain_mu(ch, 0)  # 0 dB

        # Prime DAC to first value (nice practice)
        dac_idx = 0
        delay(100*us)
        self.zotino0.set_dac([self.voltages[dac_idx]], [self.DAC_CHANNEL])

        # Timing in machine units
        saw_period_mu = self.core.seconds_to_mu((1.0 / self.SAW_FREQ_HZ) * s)
        dac_tick_mu   = saw_period_mu // self.DAC_POINTS
        if dac_tick_mu < 1:
            raise ValueError("DAC tick too small; lower SAW_FREQ_HZ or increase DAC_POINTS.")

        adc_offset_mu = self.core.seconds_to_mu(self.ADC_OFFSET_US * us)

        # Even spacing for S samples inside the DAC step (after the offset)
        rem_mu = dac_tick_mu - adc_offset_mu
        if rem_mu < self.SAMPLES_PER_STEP:
            raise ValueError("ADC_OFFSET_US too large for chosen step & samples-per-step.")
        adc_period_mu = rem_mu // self.SAMPLES_PER_STEP
        if adc_period_mu < 1:
            adc_period_mu = 1  # last resort

        # Pad so ADC branch lasts exactly one DAC step (keeps timeline neat)
        adc_used_mu = adc_offset_mu + self.SAMPLES_PER_STEP * adc_period_mu
        adc_tailpad_mu = 0
        if adc_used_mu < dac_tick_mu:
            adc_tailpad_mu = dac_tick_mu - adc_used_mu

        # One-time guard slack so the first scheduled events are safely in the future
        delay_mu(self.core.seconds_to_mu(self.GUARD_US * us))

        # Device-side buffer for all samples; pushed once at the end
        buf = [0.0] * self.n_samples
        write_idx = 0
        smp = [0.0] * 8

        # Main loop: each iteration is one DAC step
        for _ in range(self.n_steps):
            with parallel:
                # DAC branch: set voltage, then wait exactly one tick
                with sequential:
                    self.zotino0.set_dac([self.voltages[dac_idx]], [self.DAC_CHANNEL])
                    delay_mu(dac_tick_mu)

                # ADC branch: wait for settle, then take S evenly spaced samples
                with sequential:
                #     delay_mu(adc_offset_mu)
                    for _ in range(self.SAMPLES_PER_STEP):
                        self.sampler2.sample(smp)
                        buf[write_idx] = smp[self.ADC_CHANNEL]
                        write_idx += 1
                        delay_mu(adc_period_mu)
                #     if adc_tailpad_mu > 0:
                #         delay_mu(adc_tailpad_mu)

            dac_idx = (dac_idx + 1) % self.DAC_POINTS

        # Park the DAC
        self.zotino0.set_dac([0.0], [self.DAC_CHANNEL])

        # Push full dataset once
        self._push_final(buf)

