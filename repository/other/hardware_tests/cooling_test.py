from artiq.experiment import *     

class CoolingTest(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        self.setattr_device("urukul4_ch0")

    @kernel
    def run(self):  
        self.core.reset()                                       #resets core device
        self.urukul4_ch0.cpld.init()                            #initialises CPLD on channel 1
        self.urukul4_ch0.init()                                 #initialises channel 1
        delay(10 * ms)                                          #10ms delay
        
        freq = 10*MHz                                          #defines frequency variable
        amp = 0.1                                              #defines amplitude variable as an amplitude scale factor(0 to 1)
        attenuation= 3.0*dB                                     #defines attenuation variable

        self.urukul4_ch0.sw.off()                                #switches urukul channel on

        self.urukul4_ch0.set_att(attenuation)                   #writes attenuation to urukul channel
        self.urukul4_ch0.set(freq, amplitude = amp)             #writes frequency and amplitude variables to urukul channel thus outputting function
        self.urukul4_ch0.sw.on()                                #switches urukul channel on

        delay(5*ms)

        self.urukul4_ch0.set(freq/2, amplitude = amp*2)             #writes frequency and amplitude variables to urukul channel thus outputting function

        delay(5*ms)

        self.urukul4_ch0.sw.off()                                #switches urukul channel on



