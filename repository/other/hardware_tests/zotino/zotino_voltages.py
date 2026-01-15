from artiq.experiment import *     

class SetZotinoVoltages(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        self.setattr_device("zotino0")

    def prepare(self):
        self.channels = [0, 1, 2, 3]
        self.voltages = [1.0, 2.0, 3.0, 4.0]
    
    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        self.zotino0.init()
        delay(1*ms)
        self.zotino0.set_dac(self.voltages, self.channels)