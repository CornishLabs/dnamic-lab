from artiq.experiment import *
from artiq.coredevice.urukul import STA_PROTO_REV_9

class UrukulSingleToneCool(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.cpld = self.get_device("urukul4_cpld")
        self.dds  = self.get_device("urukul4_ch0")

    def prepare(self):
        self.freq = 10*MHz
        self.amp  = 0.4          # 0..1
        self.att  = 3.0*dB
        self.t_on = 2*s

        self.kernel_invariants = {"freq", "amp", "att", "t_on"}

    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()

        self.cpld.init()
        self.dds.init()

        delay(2*ms)

        # Program tone
        self.dds.set(self.freq, amplitude=self.amp)

        self.dds.set_att(self.att)

        # Turn RF switch on
        self.dds.sw.on() 

        # Hold output on for desired time
        delay(self.t_on)

        # Turn RF off
        self.dds.sw.off()
