from artiq.experiment import *


class SUServoMinimal(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_device("suservo0_ch0")

    @kernel
    def readback(self):
        # With the servo disabled and drained, reads are much less likely to collide.
        adc_v = self.suservo0.get_adc(0)
        y = self.suservo0_ch0.get_y(0)
        return adc_v, y
    
    @kernel
    def run(self):
        # Prepare core
        self.core.reset()
        self.core.break_realtime()

        #Initialize and activate SUServo
        self.suservo0.init()
        self.suservo0.set_config(enable=0)

        # Clear integrator
        self.suservo0_ch0.set_y(profile=0, y=0.) # Clear integrator
        
        # Set Sampler gain and Urukul attenuation
        g = 0
        A = 0.0
        self.suservo0.set_pgia_mu(0, g)         # set gain on Sampler channel 0 to 10^g
        self.suservo0.cplds[0].set_att(0, A)       # set attenuation on Urukul channel 0 to 0
        
        
        # Set physical parameters
        v_t = 1.8                              # target input voltage (V) for Sampler channel
        f = 80_000_000.0                        # frequency (Hz) of Urukul output
        
        o = -v_t*(10.0**(g-1))                  # offset to assign to servo to reach target voltage

        # Set PI loop parameters 
        # kp = -8.0                              # proportional gain in loop
        # ki = 0.0                              # integrator gain

        # kp = 0.0                              # proportional gain in loop
        # ki = -200_000.0                              # integrator gain

        kp = -0.8                              # proportional gain in loop
        ki = -350_000.0                              # integrator gain
        
        gl = 0.0                                # integrator gain limit
        adc_ch = 0                              # Sampler channel to read from
        
        # Input parameters, activate Urukul output (en_out=1),
        # activate PI loop (en_iir=1)
        self.suservo0_ch0.set_iir(profile=0, adc=adc_ch, kp=kp, ki=ki, g=gl)
        self.suservo0_ch0.set_dds(profile=0, frequency=f, offset=o)
        self.suservo0_ch0.set(en_out=1, en_iir=1, profile=0)
        self.suservo0.set_config(enable=1)


        # delay(20*ms)
        # self.suservo0.set_config(enable=0)
        # adc_v = self.suservo0.get_adc(0)
        # # y = self.suservo0_ch0.get_y(0)

        # self.core.break_realtime()

        # # delay(50*ms)
        # # print("Open-loop modulation result:")
        # print("  ADC0 voltage [V] =", adc_v)
        # print(s)
        # print("  y (0..1)          =", y)