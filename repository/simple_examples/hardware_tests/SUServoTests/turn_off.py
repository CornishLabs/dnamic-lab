from artiq.experiment import *


class SUServoConstantTone(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_device("suservo0_ch0")

    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()

        # Initialize Sampler + Urukuls for SU-Servo mode
        self.suservo0.init()

        # Sampler gain doesn’t matter if you’re not using IIR, but set it anyway
        self.suservo0.set_pgia_mu(0, 0)  # unity gain

        # Coarse attenuator on Urukul (set to whatever you want)
        self.suservo0.cplds[0].set_att(0, 14.0)

        # Program DDS parameters into the SU-Servo profile memory
        self.suservo0_ch0.set_dds(
            profile=0,
            frequency=80*MHz,
            offset=0.0,   # irrelevant when en_iir=0
            phase=0.0
        )

        # Set fixed amplitude scale y (0..1). Example: 0.5 = half-scale
        self.suservo0_ch0.set_y(profile=0, y=0.00)

        # RF power \propto optical power
        # Amplitude (V) \prop \sqrt optical opwer
        # Optical power \propto amp^2

        # Enable RF switch, disable IIR updates (constant amplitude)
        self.suservo0_ch0.set(en_out=1, en_iir=0, profile=0)

        # Enable global SU-Servo engine so it continuously writes DDS settings
        # (recommended in SU-Servo mode; otherwise the DDS might not get updated)
        self.suservo0.set_config(enable=1)

        # Keep tone on for some time (or omit and let it run until experiment ends)
        delay(1*ms)

        # Turn off
        # self.suservo0_ch0.set(en_out=0, en_iir=0, profile=0)
        self.suservo0.set_config(enable=0)
