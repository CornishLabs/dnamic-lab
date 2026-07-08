from artiq.experiment import *     

class SetZotinoVoltagesTest(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        self.setattr_device("zotino0")

    def prepare(self):
        self.channels = [0, 1, 2, 3]
        # self.voltages = [-0.367, 0.8, -0.112, 8.8]  #Rb
        self.voltages = [0.04, 0.47, -0.34, 8.8] # Cs
    
    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        self.zotino0.init()
        delay(1*ms)
        self.zotino0.set_dac(self.voltages, self.channels)