from artiq.experiment import *

class TakeOne(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("andor")

    def run(self):
        img = self.andor.acquire_with_ttl_exposure(0.050, wait_timeout_s=None)
        self.set_dataset("andor.image", img, broadcast=True)
