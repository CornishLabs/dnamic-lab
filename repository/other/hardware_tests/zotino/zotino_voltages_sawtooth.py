from artiq.experiment import *
import numpy
import scipy.signal as signal

class ZotinoSawtooth(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("zotino0")

    def prepare(self):
        self.period = 1*ms
        self.sample = 200
        t = numpy.linspace(0, 1, self.sample)
        self.voltages = numpy.array(
            5 * signal.sawtooth(2 * numpy.pi * t, 0.5), dtype=float
        )
        self.interval = self.period / self.sample

    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        self.zotino0.init()
        delay(1 * ms)
        counter = 0
        while True:
            self.zotino0.set_dac([self.voltages[counter]], [0])
            counter = (counter + 1) % self.sample
            delay(self.interval)
