from artiq.experiment import *     

class SetZotinoVoltages(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        self.setattr_device("zotino0")
        self.setattr_argument("A", NumberValue(precision=2, step=1, unit='mV'))
        self.setattr_argument("B", NumberValue(precision=2, step=1, unit='mV'))
        self.setattr_argument("C", NumberValue(precision=2, step=1, unit='mV'))
        self.setattr_argument("D", NumberValue(precision=2, step=1, unit='mV'))

    def prepare(self):
        self.channels = [0, 1, 2, 3,4]
        self.voltages = [self.A, self.B, self.C, self.D,0.0]
    
    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        self.zotino0.init()
        delay(1*ms)
        self.zotino0.set_dac(self.voltages, self.channels)