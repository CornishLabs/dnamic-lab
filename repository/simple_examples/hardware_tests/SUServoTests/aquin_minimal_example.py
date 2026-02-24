from artiq.experiment import *


class SUServoMinimal(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_device("suservo0_ch0")

    @kernel
    def run(self):
        # Prepare core
        self.core.reset()
        self.core.break_realtime()

        #Initialize and activate SUServo
        self.suservo0.init()
        self.suservo0.set_config(enable=0)   # Not necessary (done in init) but for completeness

        # Clear integrator
        self.suservo0_ch0.set_y(profile=0, y=0.)
        
        # Set Sampler gain and Urukul attenuation
        g = 0     # The four gain settings (0, 1, 2, 3) corresponds to gains of (1, 10, 100, 1000) respectively.
        A = 0.0
        self.suservo0.set_pgia_mu(0, g)        # set (prog. gain. inst. amp) gain on Sampler channel 0 to 10^g
        self.suservo0.cplds[0].set_att(0, A)   # set attenuation on Urukul channel 0 to 0
        
        # Set physical parameters
        v_t = 1.8                              # target input voltage (V) for Sampler channel
        f = 80_000_000.0                       # frequency (Hz) of Urukul output
        
        o = -v_t*(10.0**(g-1))                 # offset to assign to servo to reach target voltage

        # Set PI loop parameters 
        kp = -0.8                              # proportional gain in loop
        ki = -350_000.0                        # integrator gain
        
        gl = 0.0                               # integrator gain limit
        adc_ch = 0                             # Sampler channel to read from
        
        # Input parameters, activate Urukul output (en_out=1), activate PI loop (en_iir=1)
        self.suservo0_ch0.set_iir(profile=0, adc=adc_ch, kp=kp, ki=ki, g=gl) # Set profile IIR coefficients 
        self.suservo0_ch0.set_dds(profile=0, frequency=f, offset=o) # Set profile DDS coefficients
        self.suservo0_ch0.set(en_out=1, en_iir=1, profile=0) # Set to Profile 0, with RF switch (on/off) and IIR updates (on/off)
        self.suservo0.set_config(enable=1) # Start SUServo operation (the cycle of read ADC -> ... -> Output amp set -> restart)

        delay(20*ms)

        # Pause servo to readback current ADC value
        self.suservo0.set_config(enable=0)
        delay(4*us) # time to stop updates to IIR
        adc_v = self.suservo0.get_adc(0)
        y = self.suservo0_ch0.get_y(0)
        self.suservo0.set_config(enable=1)

        # Print output
        print("  ADC0 voltage [V] =", adc_v)
        print("  y (0..1)          =", y)