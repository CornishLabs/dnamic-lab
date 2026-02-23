from artiq.language.units import MHz, dB, s, ms, us, V
from artiq.language.core import delay, kernel, rpc
from artiq.language.environment import EnvExperiment
from artiq.coredevice.ad9910 import (
    RAM_DEST_ASF,
    RAM_MODE_DIRECTSWITCH,
    RAM_MODE_RAMPUP,
    AD9910,
)
from artiq.coredevice.urukul import CPLD
import numpy as np

class UrukulToneRAMExample(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        self.setattr_device("ttl0")

        self.setattr_device("dds_cpld_rsc")
        self.dds_cpld_rsc: CPLD
        self.setattr_device("dds_ch_RB1B")
        self.dds_ch_RB1B: AD9910
        self.setattr_device("dds_ch_RB2")
        self.dds_ch_RB2: AD9910
        self.setattr_device("dds_ch_RB4")
        self.dds_ch_RB4: AD9910
    

    def prepare(self):
        # Prepare pulse shape RAM for RB2 (radial -> Tukey pulse)
        # off , on , BH
        self.tukey_steps = 200
        alpha = 0.5  # Tukey shape: 0=rectangular, 1=Hann

        self.amp_logical_rb2 = [0.0,0.7] # Useful for square pulses
        
        N = self.tukey_steps
        tk = []

        for n in range(N):
            x = n / (N - 1)  # normalized position in [0, 1]

            if alpha <= 0:
                w = 1.0
            elif alpha >= 1:
                # Hann window
                w = 0.5 * (1 - np.cos(2 * np.pi * x))
            else:
                edge = alpha / 2.0
                if x < edge:
                    # rising cosine taper
                    w = 0.5 * (1 + np.cos(np.pi * (2 * x / alpha - 1)))
                elif x <= 1 - edge:
                    # flat top
                    w = 1.0
                else:
                    # falling cosine taper
                    w = 0.5 * (1 + np.cos(np.pi * (2 * x / alpha - 2 / alpha + 1)))

            tk.append(w)

        # scale (your original scaling style)
        peak = max(tk)
        if peak > 0:
            tk = [0.6 * v / peak for v in tk]

        self.amp_logical_rb2 += tk
        
        self.asf_ram_rb2 = [0] * len(self.amp_logical_rb2) # Create array to put RAM words into
        self.amp_length_rb2 = len(self.amp_logical_rb2)
        self.amp_reversed_rb2 = list(reversed(self.amp_logical_rb2)) # Create array in expected order for chip (reversed)

        # Prepare pulse shape RAM for RB4 (axial BH pulse)
        self.bh_steps = 200

        a0, a1, a2, a3 = 0.35875, 0.48829, 0.14128, 0.01168
        
        self.amp_logical_rb4 = [0.0,0.7] # Useful for square pulses
        
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

        self.amp_logical_rb4 += bh
        
        self.asf_ram_rb4 = [0] * len(self.amp_logical_rb4) # Create array to put RAM words into
        self.amp_length_rb4 = len(self.amp_logical_rb4)
        self.amp_reversed_rb4 = list(reversed(self.amp_logical_rb4)) # Create array in expected order for chip (reversed)

    @kernel
    def init_dds(self, dds):
        dds.init()
        dds.set_att(6.*dB)
        dds.cfg_sw(False)

    @kernel
    def configure_RB24_ram_mode(self, rb2_dds, rb4_dds):
        ##### RB2

        rb2_dds.set_cfr1(ram_enable=0) # Control Function Register 1
        rb2_dds.io_update.pulse_mu(8)

        # 1) Loader profile (also the initial profile we start in)
        # This loads all the RAM at once 
        LOADER = 0
        rb2_dds.set_profile(LOADER)
        rb2_dds.set_profile_ram(start=0, end=self.amp_length_rb4-1, step=1, profile=LOADER,
                            mode=RAM_MODE_RAMPUP)
        rb2_dds.io_update.pulse_mu(8)

        # 2) Load once
        rb2_dds.amplitude_to_ram(self.amp_reversed_rb2, self.asf_ram_rb2) # Reverse the logical list to get nice indices
        rb2_dds.write_ram(self.asf_ram_rb2)
        self.core.break_realtime()
        
        # 3) Set profiles
        for profile, start, end, step, mode in [
            (1,1,1,                  1,RAM_MODE_DIRECTSWITCH),
            (2,2,2+self.tukey_steps-1, 10,RAM_MODE_RAMPUP),
            (0,0,0,                  1,RAM_MODE_DIRECTSWITCH)
            ]:
            rb2_dds.set_profile_ram(
                start=start, end=end,
                step=step, profile=profile, mode=mode
            )
            rb2_dds.io_update.pulse_mu(8)
        
        
        rb2_dds.set(frequency=5*MHz, ram_destination=RAM_DEST_ASF) # Set what the frequency is, and what the RAM does (ASF)
        rb2_dds.set_cfr1(ram_enable=1, ram_destination=RAM_DEST_ASF) # Enable RAM, Pass osk_enable=1 to set_cfr1() if it is not an amplitude RAM
        rb2_dds.io_update.pulse_mu(8) # Write to CPLD

        ##### RB4

        rb4_dds.set_cfr1(ram_enable=0) # Control Function Register 1
        rb4_dds.io_update.pulse_mu(8)

        # 1) Loader profile (also the initial profile we start in)
        # This loads all the RAM at once 
        LOADER = 0
        rb4_dds.set_profile(LOADER)
        rb4_dds.set_profile_ram(start=0, end=self.amp_length_rb4-1, step=1, profile=LOADER,
                            mode=RAM_MODE_RAMPUP)
        rb4_dds.io_update.pulse_mu(8)

        # 2) Load once
        rb4_dds.amplitude_to_ram(self.amp_reversed_rb4, self.asf_ram_rb4) # Reverse the logical list to get nice indices
        rb4_dds.write_ram(self.asf_ram_rb4)
        self.core.break_realtime()
        
        # 3) Set profiles
        for profile, start, end, step, mode in [
            (1,1,1,                  1,RAM_MODE_DIRECTSWITCH),
            (2,2,2+self.bh_steps-1, 10,RAM_MODE_RAMPUP),
            (0,0,0,                  1,RAM_MODE_DIRECTSWITCH)
            ]:
            rb4_dds.set_profile_ram(
                start=start, end=end,
                step=step, profile=profile, mode=mode
            )
            rb4_dds.io_update.pulse_mu(8)
        
        
        rb4_dds.set(frequency=5*MHz, ram_destination=RAM_DEST_ASF) # Set what the frequency is, and what the RAM does (ASF)
        rb4_dds.set_cfr1(ram_enable=1, ram_destination=RAM_DEST_ASF) # Enable RAM, Pass osk_enable=1 to set_cfr1() if it is not an amplitude RAM
        rb4_dds.io_update.pulse_mu(8) # Write to CPLD

    @kernel
    def configure_RB1AB_single_tone_mode(self, dds):
        dds.set(frequency=5*MHz, phase=0.0, amplitude=0.0, profile=0)
        dds.set(frequency=5*MHz, phase=0.0, amplitude=0.87, profile=1)
        dds.set(frequency=5*MHz, phase=0.0, amplitude=0.67, profile=2)
        dds.set(frequency=5*MHz, phase=0.0, amplitude=0.37, profile=3)
        dds.set(frequency=5*MHz, phase=0.0, amplitude=0.27, profile=4)
        dds.set(frequency=5*MHz, phase=0.0, amplitude=0.30, profile=5)
        dds.set(frequency=5*MHz, phase=0.0, amplitude=0.35, profile=6)
        dds.set(frequency=5*MHz, phase=0.0, amplitude=0.31, profile=7)
        
    
    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        # Initialise and setup urukul cpld, channels, and attenuators
        self.dds_cpld_rsc.init()
        self.init_dds(self.dds_ch_RB1B)
        self.core.break_realtime()
        self.init_dds(self.dds_ch_RB2)
        self.core.break_realtime()
        self.init_dds(self.dds_ch_RB4)

        self.core.break_realtime()
        # Setup DDS RAM mode, upload RAM, and set profile registers
        self.configure_RB24_ram_mode(self.dds_ch_RB2,self.dds_ch_RB4)
        self.configure_RB1AB_single_tone_mode(self.dds_ch_RB1B)

        self.core.break_realtime()
        # Profile playback
        # Note that set_profile actually advances the timeline
        # (by 0.86us when I measured it) so the delays for direct profile switches
        # are not necessarily the delays you expect. Therefore be careful when trying
        # to do a preciscely timed square pulse with the RAM_MODE_DIRECTSWITCH mode.
        # This is because the configuration register of the CPLD is written over SPI
        # Which *then* changes the profile pins internally.
        self.dds_ch_RB1B.cfg_sw(True) # Enable RF switch
        self.dds_ch_RB2.cfg_sw(True) # Enable RF switch
        self.dds_ch_RB4.cfg_sw(True) # Enable RF switch

        self.ttl0.pulse(1*us) # scope trigger

        for i in range(5): # Repeat 5 times
            # Axial n-4
            self.dds_ch_RB1B.set_profile(6)
            self.dds_ch_RB4.set_profile(2)
            delay(20*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(20*us)
            # Radial x
            self.dds_ch_RB1B.set_profile(2)
            self.dds_ch_RB2.set_profile(2)
            delay(20*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(20*us)
            # Axial n-4
            self.dds_ch_RB1B.set_profile(6)
            self.dds_ch_RB4.set_profile(2)
            delay(20*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(20*us)
            # Radial y
            self.dds_ch_RB1B.set_profile(1)
            self.dds_ch_RB2.set_profile(2)
            delay(20*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(20*us)

        self.dds_ch_RB1B.cfg_sw(False) # Enable RF switch
        self.dds_ch_RB2.cfg_sw(False) # Enable RF switch
        self.dds_ch_RB4.cfg_sw(False) # Enable RF switch


