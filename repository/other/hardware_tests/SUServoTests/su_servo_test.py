from artiq.experiment import *


class SUServoTest(EnvExperiment):
    """
    Constant SU Servo.
    In SU-Servo configuration, amplitude of the Urukul DDS output can be 
    controlled with the Sampler ADC input through PI control, characterised 
    by the following transfer function:
    H_s = k_p + k_i/(s+k_i/g)

    In the following example, the DDS amplitude is set proportionally to the 
    ADC input from Sampler. We initialize SU-Servo and all channels first. 
    Note that the programmable gain of the Sampler is 100 = 1 and the input 
    range is [-10V, 10V]
    """

    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_device("suservo0_ch0")

    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        self.suservo0.init()
        self.suservo0.set_pgia_mu(0, 0) # unity gain
        self.suservo0.cplds[0].set_att(0, 15.)
        self.suservo0_ch0.set_y(profile=0, y=0.) # Clear integrator
        
        # Next, we set up the PI control as an IIR filter.
        # It has -1 proportional gain kp and no integrator gain ki

        self.suservo0_ch0.set_iir(
            profile=0,
            adc=0, # take data from Sampler channel 0
            kp=-1., # -1 P gain
            ki=0./s, # no integrator gain
            g=0., # no integrator gain limit
            delay=0. # no IIR update delay after enabling
        )

        # Then, configure the DDS frequency to 10 MHz with 3V input offset. 
        # When input voltage ≥ offset voltage, the DDS
        # output amplitude is 0.

        self.suservo0_ch0.set_dds(
            profile=0,
            offset=-.1, # 1 V with above PGIA settings
            frequency=10*MHz,
            phase=0.
        )

        # SU-Servo encodes the ADC voltage on a linear scale [-1, 1]. 
        # Therefore, 3V is converted to 0.3. Note that the ASF of
        # all DDS channels ss capped at 1.0 and the amplitude clips 
        # when ADC input ≤ −7V with the above IIR filter.

        # Finally, enable the SU-Servo channel with the IIR filter programmed beforehand

        self.suservo0_ch0.set(en_out=1, en_iir=1, profile=0)
        self.suservo0.set_config(enable=1)

        # A 10 MHz DDS signal is generated from the example above, with amplitude 
        # controllable by ADC.
