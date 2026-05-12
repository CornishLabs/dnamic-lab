from artiq.language.units import MHz, dB, s, ms, us, V
from artiq.language.core import delay, kernel, rpc, delay_mu
from artiq.language.environment import EnvExperiment
from artiq.coredevice.ad9910 import (
    RAM_DEST_ASF,
    RAM_MODE_DIRECTSWITCH,
    RAM_MODE_RAMPUP,
    AD9910,
)
from artiq.coredevice.core import Core
from artiq.coredevice.urukul import CPLD
from artiq.coredevice.ttl import TTLOut
import numpy as np

import scipy.signal as signal


# class SetZotinoVoltages(EnvExperiment):
    
#     def build(self):
#         self.setattr_device("core")
#         self.setattr_device("zotino0")

#     def prepare(self):
#         self.channels = [0, 1, 2, 3]
#         self.voltages = [1.0, 2.0, 3.0, 4.0]
    
#     @kernel
#     def run(self):
#         self.core.reset()
#         self.core.break_realtime()
#         self.zotino0.init()
#         delay(1*ms)
#         self.zotino0.set_dac(self.voltages, self.channels)


class ZotinoSawtooth(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("zotino0")

    def prepare(self):
        self.period = 10*ms
        self.sample = 2000
        t = np.linspace(0, 1, self.sample)
        self.voltages = np.array(
            5 * signal.sawtooth(2 * np.pi * t, 0.01), dtype=float
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
