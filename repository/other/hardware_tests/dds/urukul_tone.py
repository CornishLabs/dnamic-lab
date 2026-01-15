from artiq.experiment import *     

class UrukulTone(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        self.setattr_device("urukul0_ch0")

    @kernel
    def run(self):  
        self.core.reset()                                       #resets core device
        self.urukul0_ch0.cpld.init()                            #initialises CPLD on channel 1
        self.urukul0_ch0.init()                                 #initialises channel 1
        delay(10 * ms)                                          #10ms delay
        
        freq = 10*MHz                                          #defines frequency variable
        amp = 0.1                                              #defines amplitude variable as an amplitude scale factor(0 to 1)
        attenuation= 3.0*dB                                     #defines attenuation variable

        self.urukul0_ch0.set_att(attenuation)                   #writes attenuation to urukul channel
        self.urukul0_ch0.set(freq, amplitude = amp)             #writes frequency and amplitude variables to urukul channel thus outputting function
        self.urukul0_ch0.sw.on()                                #switches urukul channel on