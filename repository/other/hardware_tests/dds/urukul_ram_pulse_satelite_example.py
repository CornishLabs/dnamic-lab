from artiq.experiment import *
from artiq.coredevice.ad9910 import (
    RAM_DEST_ASF,
    RAM_MODE_DIRECTSWITCH,
    RAM_MODE_RAMPUP,
    RAM_MODE_CONT_RAMPUP
)
import numpy as np

class UrukulToneRAMExample(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        self.setattr_device("ttl0")
        self.dds = self.get_device("urukul4_ch0")
        self.cpld = self.get_device("urukul4_cpld")
        
    def prepare(self):
        # off , on , BH
        self.amp_logical = [0.0,0.7]
        self.bh_steps = 200

        a0, a1, a2, a3 = 0.35875, 0.48829, 0.14128, 0.01168
        
        twopi = 2*np.pi
        bh = []
        for n in range(self.bh_steps):
            x = n/(self.bh_steps-1)
            w = a0 - a1*np.cos(twopi*x) + a2*np.cos(2*twopi*x) - a3*np.cos(3*twopi*x)
            bh.append(w)
        # scale
        peak = max(bh)
        if peak > 0:
            bh = [0.6*v/peak for v in bh]

        self.amp_logical += bh

        #               7    6    5    4    3    2    1    0     indices when writing ram profiles set_profile_ram
        # self.amp = [0.0, 0.0, 0.1, 0.7, 0.1, 0.5, 0.5, 0.0]    this will be played in right-to-left order if sent like this
        
        self.asf_ram = [0] * len(self.amp_logical) # Create array to put RAM words into
        self.amp_length = len(self.amp_logical)
        self.amp_reversed = list(reversed(self.amp_logical)) # Create array in expected order for chip (reversed)

    @kernel
    def init_dds(self, dds):
        dds.init()
        dds.set_att(6.*dB)
        dds.cfg_sw(True)

    @kernel
    def configure_ram_mode(self, dds):
        dds.set_cfr1(ram_enable=0) # Control Function Register 1
        self.cpld.io_update.pulse_mu(8)

        # 1) Loader profile
        LOADER = 7
        self.cpld.set_profile(LOADER)
        dds.set_profile_ram(start=0, end=self.amp_length-1, step=1, profile=LOADER,
                            mode=RAM_MODE_RAMPUP)
        self.cpld.io_update.pulse_mu(8)

        # 2) Load once
        dds.amplitude_to_ram(self.amp_reversed, self.asf_ram) # Reverse the logical list to get nice indices
        dds.write_ram(self.asf_ram)
        
        # 3) Load profiles (the last one will be what we start in)
        for profile, start,end, step, mode in [
            (1,1,1,                  1,RAM_MODE_DIRECTSWITCH),
            (2,2,2+self.bh_steps-1, 10,RAM_MODE_RAMPUP),
            (0,0,0,                  1,RAM_MODE_DIRECTSWITCH)
            ]:
            self.cpld.set_profile(profile) # Must set the profile so it goes to the right register (even though we repeat below)
            dds.set_profile_ram(
                start=start, end=end,
                step=step, profile=profile, mode=mode
            )
            self.cpld.io_update.pulse_mu(8)
        
        
        dds.set(frequency=5*MHz, ram_destination=RAM_DEST_ASF) # Set what the frequency is, and what the RAM does (ASF)
        dds.set_cfr1(ram_enable=1, ram_destination=RAM_DEST_ASF) # Enable RAM, Pass osk_enable=1 to set_cfr1() if it is not an amplitude RAM
        self.cpld.io_update.pulse_mu(8) # Write to CPLD
    
    @kernel
    def run(self):
        self.core.reset()
        self.ttl0.output()
        self.cpld.init()
        self.init_dds(self.dds)

        self.configure_ram_mode(self.dds)

        self.ttl0.on()
        self.cpld.set_profile(1)
        delay(6*us)
        self.cpld.set_profile(0)
        delay(4*us)
        self.cpld.set_profile(2)
        delay(7*us)
        self.cpld.set_profile(0)
        delay(4*us)
        self.cpld.set_profile(2)
        self.ttl0.off()
