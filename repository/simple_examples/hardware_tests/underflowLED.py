from artiq.experiment import *

class BlinkToUnderflow(EnvExperiment):
    """Change the pulse time and delay time to overwhelm the processor speed and cause a RTIO underflow"""
    def build(self):
        self.setattr_device("core")
        self.setattr_device("led0")

    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        for i in range(1000):
            self.led0.pulse(.7*us)
            delay(.7*us)
