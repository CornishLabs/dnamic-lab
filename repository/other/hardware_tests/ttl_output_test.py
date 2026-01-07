from artiq.experiment import *


class TTL_Output_On_Off_Pulse(EnvExperiment):
    """TTL Output On, Off, Pulse"""

    def build(self):
        self.setattr_device("core")
        self.setattr_device("ttl0")

    @kernel
    def run(self):
        self.core.reset()
        self.ttl0.output()

        delay(20 * us)
        self.ttl0.on()
        delay(5 * ms)
        self.ttl0.off()
