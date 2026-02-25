import numpy as np

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

from ndscan.experiment import Fragment, ExpFragment
from ndscan.experiment.parameters import IntParam, FloatParam

"""
      z || RB4 (antiparallel)
      
      |
      |
      +------ y || RB2 (parallel)
     /
    /
   x || B || RB1A/B (antiparallel)

   
  P6  110-------111 P7
     / |       / |
    /  |      /  |
P2 010-----011 P3|
   |   |     |   |
   |P4 100---|--101 P5
   | /       | /
   |/        |/
P0 000-------001 P1
"""

# SET ASSUMING 400 STEPS IN RAM FOR PULSE (Tukey)
RB2_STEPS = [1, 42, 62, 92, 50, 1, 1, 1]
#RB2_PROF_TIMES_US = [4e-3*400*steps for steps in RB2_STEPS]
RB2_PROFILE_HUMAN_NAMES = ['off','R1','R1longer','unused','R2','unused','RbFlip','CsFlip']
RB2_AMP_SCALE = 0.47

# SET ASSUMING 400 STEPS IN RAM FOR PULSE (BH)
RB4_STEPS = [1, 142, 215, 175, 210, 158, 170, 312]
#RB4_PROF_TIMES_US = [4e-3*400*steps for steps in RB4_STEPS]
RB4_PROFILE_HUMAN_NAMES = ['off','unused','Zm1Weird','Zm1','Zm2','Zm3','Zm4','unused']
RB4_AMP_SCALE = 0.8

RB1B_FREQS_MHZ = [120.0, 100.063, 100.032, 99.988, 100.0095, 100.032, 100.0545, 99.829]
RB1B_AXIAL_AMP = 0.37
RB1B_RADIAL_AMP = 0.87
RB1B_AMPS = [0.0,RB1B_RADIAL_AMP, RB1B_RADIAL_AMP, RB1B_AXIAL_AMP, RB1B_AXIAL_AMP, RB1B_AXIAL_AMP, RB1B_AXIAL_AMP, RB1B_RADIAL_AMP]
RB1B_PROFILE_HUMAN_NAMES = ['off', 'R2', 'R1', 'Zm1', 'Zm2', 'Zm3', 'Zm4','RbFlip']

OP_TIME_US = 15

OP12_FREQ_MHZ = 110.0
OP22_FREQ_MHZ = 200.0
OP34_FREQ_MHZ = 110.0
OP44_FREQ_MHZ = 200.0

OP12_AMP = 0.5
OP22_AMP = 0.5
OP34_AMP = 0.5
OP44_AMP = 0.5

class UrukulRSCExample(Fragment):
    
    def build_fragment(self):
        # ARGUMENTS

        ## OP settings
        self.setattr_param('OP_time_us',
                           FloatParam,
                           "How long to OP between Raman pulses (us)",
                           unit=us,
                           default=OP_TIME_US, min=1.0, max=200.0
                        )
        
        self.setattr_param('OP12_frequency_MHz',
            FloatParam,
            "Frequency played for OP goes to AOM",
            unit=MHz,
            default=OP12_FREQ_MHZ, min=1.0, max=200.0
        )
        
        self.setattr_param('OP22_frequency_MHz',
            FloatParam,
            "Frequency played for OP goes to AOM",
            unit=MHz,
            default=OP22_FREQ_MHZ, min=1.0, max=200.0
        )

        self.setattr_param('OP34_frequency_MHz',
            FloatParam,
            "Frequency played for OP goes to AOM",
            unit=MHz,
            default=OP34_FREQ_MHZ, min=1.0, max=200.0
        )
        
        self.setattr_param('OP44_frequency_MHz',
            FloatParam,
            "Frequency played for OP goes to AOM",
            unit=MHz,
            default=OP44_FREQ_MHZ, min=1.0, max=200.0
        )

        self.setattr_param('OP12_Amp',
            FloatParam,
            "Amp relative to full scale (0-1) to AOM",
            unit=MHz,
            default=OP12_AMP, min=0.0, max=1.0
        )
        
        self.setattr_param('OP22_Amp',
            FloatParam,
            "Amp relative to full scale (0-1) to AOM",
            unit=MHz,
            default=OP22_AMP, min=0.0, max=1.0
        )

        self.setattr_param('OP34_Amp',
            FloatParam,
            "Amp relative to full scale (0-1) to AOM",
            unit=MHz,
            default=OP34_AMP, min=0.0, max=1.0
        )
        
        self.setattr_param('OP44_Amp',
            FloatParam,
            "Amp relative to full scale (0-1) to AOM",
            unit=MHz,
            default=OP44_AMP, min=0.0, max=1.0
        )
        
        self.setattr_param('OP_time_us',
            FloatParam,
            "How long to OP between Raman pulses (us)",
            unit=us,
            default=OP_TIME_US, min=1.0, max=200.0
        )

        ## Single tone RSC beams
        for i in range(1,8):
            prof_name = RB1B_PROFILE_HUMAN_NAMES[i]
            self.setattr_param(f'RB1B_{prof_name}_frequency_MHz', 
                    FloatParam,
                    "Frequency played for profile goes to AOM",
                    unit=MHz,
                    default=RB1B_FREQS_MHZ[i], min=1, max=5000,  # Not sure what the actual max is
                )

            self.setattr_param(f'RB1B_{prof_name}_amp', 
                    FloatParam,
                    "Amp played for profile (0-1, relative to full scale)",
                    default=RB1B_AMPS[i], min=0.0, max=1.0,  # Not sure what the actual max is
                )

        ## RAM mode RSC beams
        for i in range(1,8):
            prof_name = RB2_PROFILE_HUMAN_NAMES[i]
            self.setattr_param(f'RB2_{prof_name}_cycles_per_step', 
                               IntParam, 
                               "How many cycles of SYNC_CLK (normally 4ns/cycle) before changing RAM step",
                               default=RB2_STEPS[i], min=1, max=5000,  # Not sure what the actual max is
                            )
            
        self.setattr_param('RB2_amp_scale', 
                    FloatParam, "Scale RAM pulse by this factor (scaling an envelope with a peak of 1)",
                    default=RB2_AMP_SCALE, min=0.0, max=1.0
                    )
        
        for i in range(1,8):
            prof_name = RB4_PROFILE_HUMAN_NAMES[i]
            self.setattr_param(f'RB4_{prof_name}_cycles_per_step', 
                               IntParam, 
                               "How many cycles of SYNC_CLK (normally 4ns/cycle) before changing RAM step",
                               default=RB4_STEPS[i], min=1, max=5000,  # Not sure what the actual max is
                            )
        self.setattr_param('RB4_amp_scale', 
            FloatParam, "Scale RAM pulse by this factor (scaling an envelope with a peak of 1)",
            default=RB4_AMP_SCALE, min=0.0, max=1.0
        )
        
        for prof_name in RB4_PROFILE_HUMAN_NAMES:
            self.setattr_param(f'RB4_{prof_name}_cycles_per_step', IntParam)

        # DEVICES
        self.setattr_device("core")
        self.core: Core
        self.setattr_device("ttl0")
        self.ttl0: TTLOut
        
        self.dds_cpld_rsc: CPLD = self.get_device("dds_cpld_rsc")
        self.dds_ch_RB1A: AD9910 = self.get_device("dds_ch_RB1A")
        self.dds_ch_RB1B: AD9910 = self.get_device("dds_ch_RB1B")
        self.dds_ch_RB2: AD9910 = self.get_device("dds_ch_RB2")
        self.dds_ch_RB4: AD9910 = self.get_device("dds_ch_RB4")

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {"dds_cpld_rsc", "dds_ch_RB1A", "dds_ch_RB1B", "dds_ch_RB2", "dds_ch_RB4"}

    def host_setup(self):
        super().host_setup()

        # Lazy prepare (could be in prepare instead)
        self.compute_full_scale_RAM_profiles()
        
    
    def compute_full_scale_RAM_profiles(self):
        # Convention is that all RAM profiles are:
        # off, on, [BH]

        # Prepare pulse shape RAM for RB2 (radial -> Tukey pulse)
        
        self.tukey_steps = 400
        alpha = 0.5  # Tukey shape: 0=rectangular, 1=Hann

        self.amp_logical_rb2 = [0.0,0.7] # Useful for direct switch square pulses
        
        tk = []
        for n in range(self.tukey_steps):
            x = n / (self.tukey_steps - 1)  # normalized position in [0, 1]

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

        self.amp_logical_rb2 += tk
        
        self.asf_ram_rb2 = [0] * len(self.amp_logical_rb2) # Create array to put RAM words into
        self.amp_length_rb2 = len(self.amp_logical_rb2)
        self.amp_reversed_rb2 = list(reversed(self.amp_logical_rb2)) # Create array in expected order for chip (reversed)

        # Prepare pulse shape RAM for RB4 (axial BH pulse)
        self.bh_steps = 400

        a0, a1, a2, a3 = 0.35875, 0.48829, 0.14128, 0.01168
        
        self.amp_logical_rb4 = [0.0,0.7] # Useful for square pulses
        
        twopi = 2*np.pi
        bh = []
        for n in range(self.bh_steps):
            x = n/(self.bh_steps-1)
            w = a0 - a1*np.cos(twopi*x) + a2*np.cos(2*twopi*x) - a3*np.cos(3*twopi*x)
            bh.append(w)

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

        rb2_dds.amplitude_to_ram(self.amp_reversed_rb2, self.asf_ram_rb2) # Reverse the logical list to get nice indices
        self.core.break_realtime()

        if self.amp_length_rb2 <= 420:
            LOADER = 0
            rb2_dds.set_profile(LOADER)
            rb2_dds.set_profile_ram(start=0, end=self.amp_length_rb2-1, step=1, profile=LOADER,
                                mode=RAM_MODE_RAMPUP)
            rb2_dds.io_update.pulse_mu(8)
            rb2_dds.write_ram(self.asf_ram_rb2)
            self.core.break_realtime()
        else:
            # Need to upload in blocks
            # RAM upload on satelites is flakey for over ~400 points, so upload in blocks
            raise


        # One step will take 
        # DeltaT = 4M / SysClockFreq
        # where M is the 'step number' below
        # unless weird things are happening, the sysclk is 1GHz
        # so smallest DeltaT is 4ns for a step of 1
        
        # 3) Set profiles
        for profile, start, end, step, mode in [
            (1,2,2+self.tukey_steps-1, RB2_STEPS[1], RAM_MODE_RAMPUP),
            (2,2,2+self.tukey_steps-1, RB2_STEPS[2], RAM_MODE_RAMPUP),
            (3,2,2+self.tukey_steps-1, RB2_STEPS[3], RAM_MODE_RAMPUP),
            (4,2,2+self.tukey_steps-1, RB2_STEPS[4], RAM_MODE_RAMPUP),
            (5,1,1,                     1, RAM_MODE_DIRECTSWITCH), # On
            (0,0,0,                     1, RAM_MODE_DIRECTSWITCH)  # Off
            ]:
            rb2_dds.set_profile_ram(
                start=start, end=end,
                step=step, profile=profile, mode=mode
            )
            rb2_dds.io_update.pulse_mu(8)
        
        
        rb2_dds.set(frequency=0.5*MHz, ram_destination=RAM_DEST_ASF)   # Set what the frequency is, and what the RAM does (ASF)
        rb2_dds.set_cfr1(ram_enable=1, ram_destination=RAM_DEST_ASF) # Enable RAM, Pass osk_enable=1 to set_cfr1() if it is not an amplitude RAM
        rb2_dds.io_update.pulse_mu(8) # Write to CPLD

        ##### RB4

        rb4_dds.set_cfr1(ram_enable=0) # Control Function Register 1
        rb4_dds.io_update.pulse_mu(8)

        # 1) Loader profile (also the initial profile we start in)
        # This loads all the RAM at once 

        # 2) Load once
        rb4_dds.amplitude_to_ram(self.amp_reversed_rb4, self.asf_ram_rb4) # Reverse the logical list to get nice indices
        self.core.break_realtime()
        
        if self.amp_length_rb4 <= 420:
            LOADER = 0
            rb4_dds.set_profile(LOADER)
            rb4_dds.set_profile_ram(start=0, end=self.amp_length_rb4-1, step=1, profile=LOADER,
                                mode=RAM_MODE_RAMPUP)
            rb4_dds.io_update.pulse_mu(8)
            rb4_dds.write_ram(self.asf_ram_rb4)
            self.core.break_realtime()
        else:
            # Need to upload in blocks
            # RAM upload on satelites is flakey for over ~400 points, so upload in blocks
            raise
        
        # 3) Set profiles
        for profile, start, end, step, mode in [
            (1,2,2+self.bh_steps-1, RB4_STEPS[1],RAM_MODE_RAMPUP),
            (2,2,2+self.bh_steps-1, RB4_STEPS[2],RAM_MODE_RAMPUP),
            (3,2,2+self.bh_steps-1, RB4_STEPS[3],RAM_MODE_RAMPUP),
            (4,2,2+self.bh_steps-1, RB4_STEPS[4],RAM_MODE_RAMPUP),
            (5,2,2+self.bh_steps-1, RB4_STEPS[5],RAM_MODE_RAMPUP),
            (6,2,2+self.bh_steps-1, RB4_STEPS[6],RAM_MODE_RAMPUP),
            (7,2,2+self.bh_steps-1, RB4_STEPS[7],RAM_MODE_RAMPUP),
            (0,0,0,                  1,          RAM_MODE_DIRECTSWITCH) # Off
            ]:
            rb4_dds.set_profile_ram(
                start=start, end=end,
                step=step, profile=profile, mode=mode
            )
            rb4_dds.io_update.pulse_mu(8)
        
        
        rb4_dds.set(frequency=0.5*MHz, ram_destination=RAM_DEST_ASF) # Set what the frequency is, and what the RAM does (ASF)
        rb4_dds.set_cfr1(ram_enable=1, ram_destination=RAM_DEST_ASF) # Enable RAM, Pass osk_enable=1 to set_cfr1() if it is not an amplitude RAM
        rb4_dds.io_update.pulse_mu(8) # Write to CPLD

    @kernel
    def configure_RB1AB_single_tone_mode(self, dds):
        delay(20*us)
        dds.set_profile(0) # Set profile pins to default
        delay(20*us)
        dds.set(frequency=0.5*MHz, phase=0.0, amplitude=RB1B_AMPS[1], profile=1)
        dds.set(frequency=0.5*MHz, phase=0.0, amplitude=RB1B_AMPS[2], profile=2)
        dds.set(frequency=0.5*MHz, phase=0.0, amplitude=RB1B_AMPS[3], profile=3)
        dds.set(frequency=0.5*MHz, phase=0.0, amplitude=RB1B_AMPS[4], profile=4)
        dds.set(frequency=0.5*MHz, phase=0.0, amplitude=RB1B_AMPS[5], profile=5)
        dds.set(frequency=0.5*MHz, phase=0.0, amplitude=RB1B_AMPS[6], profile=6)
        dds.set(frequency=0.5*MHz, phase=0.0, amplitude=RB1B_AMPS[7], profile=7)

        dds.set(frequency=0.5*MHz, phase=0.0, amplitude=0.0, profile=0) # Off
        delay(20*us) #Give some time for io update after
        dds.set_profile(0) # Set profile pins to default
        delay(20*us)

    @kernel
    def device_setup(self):
        self.device_setup_subfragments() # Should be NO-OP for this fragment

        # TODO: Make this do nothing if no params changed
                
        # Setup DDS RAM mode, upload RAM, and set profile registers
        self.configure_RB24_ram_mode(self.dds_ch_RB2,self.dds_ch_RB4)
        delay(10*us)
        self.configure_RB1AB_single_tone_mode(self.dds_ch_RB1B)

        self.core.break_realtime()

    
    @kernel
    def play_rsc_pulses(self):
        """
        Assumptions:
        - DDS CPLD, DDS Chs (AD9910s) are initialised, and that they have valid SYNC_CLK w.r.t RTIO
            (see https://forum.m-labs.hk/d/1221-urukul-pr9-set-profile-non-deterministic-intermediate-hamming-path-activation)
        - Profile settings (Profile settings, single tone/ram mode setup) are valid NOW. 
            This fragment sets them in device_setup(). But this means caution must be 
            applied if this method wanted to be called multiple times with different settings
            in each sequence, as the last fragment set in build_fragment() will take precedence.
        """

        # Profile playback
        # Note that set_profile actually advances the timeline
        # (by 0.86us when I measured it) so the delays for direct profile switches
        # are not necessarily the delays you expect. Therefore be careful when trying
        # to do a preciscely timed square pulse with the RAM_MODE_DIRECTSWITCH mode.
        # This is because the configuration register of the CPLD is written over SPI
        # Which *then* changes the profile pins internally.

        # Configure OP beams


        self.dds_ch_RB1B.cfg_sw(True) # Enable RF switch
        self.dds_ch_RB2.cfg_sw(True) # Enable RF switch
        self.dds_ch_RB4.cfg_sw(True) # Enable RF switch

        # Set RB1s first as the SPI delay of ~0.86us will give them a natural buffer
        for i in range(6): # Repeat 6 times
            # Axial n-4
            self.dds_ch_RB1B.set_profile(6)
            self.dds_ch_RB4.set_profile(6)
            delay(RB4_PROF_TIMES_US[6]*us) # This is the time for the pulse to play
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME) # This is the time for OP
            
            # Radial x
            self.dds_ch_RB1B.set_profile(2)
            self.dds_ch_RB2.set_profile(1)
            delay(RB2_PROF_TIMES_US[1]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)
            
            # Axial n-4
            self.dds_ch_RB1B.set_profile(6)
            self.dds_ch_RB4.set_profile(6)
            delay(RB4_PROF_TIMES_US[6]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME)
            
            # Radial y
            self.dds_ch_RB1B.set_profile(1)
            self.dds_ch_RB2.set_profile(4)
            delay(RB2_PROF_TIMES_US[4]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)

        for i in range(10): # Repeat 10 times
            # Axial n-3
            self.dds_ch_RB1B.set_profile(5)
            self.dds_ch_RB4.set_profile(5)
            delay(RB4_PROF_TIMES_US[5]*us) # This is the time for the pulse to play
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME) # This is the time for OP
            
            # Radial x
            self.dds_ch_RB1B.set_profile(2)
            self.dds_ch_RB2.set_profile(1)
            delay(RB2_PROF_TIMES_US[1]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)
            
            # Axial n-3
            self.dds_ch_RB1B.set_profile(5)
            self.dds_ch_RB4.set_profile(5)
            delay(RB4_PROF_TIMES_US[5]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME)
            
            # Radial y
            self.dds_ch_RB1B.set_profile(1)
            self.dds_ch_RB2.set_profile(4)
            delay(RB2_PROF_TIMES_US[4]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)
        
        for i in range(10): # Repeat 10 times
            # Axial n-2
            self.dds_ch_RB1B.set_profile(4)
            self.dds_ch_RB4.set_profile(4)
            delay(RB4_PROF_TIMES_US[4]*us) # This is the time for the pulse to play
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME) # This is the time for OP
            
            # Radial x
            self.dds_ch_RB1B.set_profile(2)
            self.dds_ch_RB2.set_profile(1)
            delay(RB2_PROF_TIMES_US[1]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)
            
            # Axial n-2
            self.dds_ch_RB1B.set_profile(4)
            self.dds_ch_RB4.set_profile(4)
            delay(RB4_PROF_TIMES_US[4]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME)
            
            # Radial y
            self.dds_ch_RB1B.set_profile(1)
            self.dds_ch_RB2.set_profile(4)
            delay(RB2_PROF_TIMES_US[4]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)
        
        for i in range(15): # Repeat 15 times
            # Axial n-2
            self.dds_ch_RB1B.set_profile(4)
            self.dds_ch_RB4.set_profile(4)
            delay(RB4_PROF_TIMES_US[4]*us) # This is the time for the pulse to play
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME) # This is the time for OP
            
            # Radial x (longer)
            self.dds_ch_RB1B.set_profile(2)
            self.dds_ch_RB2.set_profile(2)
            delay(RB2_PROF_TIMES_US[2]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)
            
            # Axial n-1
            self.dds_ch_RB1B.set_profile(3)
            self.dds_ch_RB4.set_profile(3)
            delay(RB4_PROF_TIMES_US[3]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME)
            
            # Radial y
            self.dds_ch_RB1B.set_profile(1)
            self.dds_ch_RB2.set_profile(4)
            delay(RB2_PROF_TIMES_US[4]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)

            # Axial n-1 (weird)
            self.dds_ch_RB1B.set_profile(3)
            self.dds_ch_RB4.set_profile(2)
            delay(RB4_PROF_TIMES_US[2]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME)

            # Radial x
            self.dds_ch_RB1B.set_profile(2)
            self.dds_ch_RB2.set_profile(1)
            delay(RB2_PROF_TIMES_US[1]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)

            # Axial n-2
            self.dds_ch_RB1B.set_profile(4)
            self.dds_ch_RB4.set_profile(4)
            delay(RB4_PROF_TIMES_US[4]*us) 
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME)

            # Radial x (longer)
            self.dds_ch_RB1B.set_profile(2)
            self.dds_ch_RB2.set_profile(2)
            delay(RB2_PROF_TIMES_US[2]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)
            
            # Axial n-1
            self.dds_ch_RB1B.set_profile(3)
            self.dds_ch_RB4.set_profile(3)
            delay(RB4_PROF_TIMES_US[3]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB4.set_profile(0)
            delay(OP_TIME)

            # Radial y
            self.dds_ch_RB1B.set_profile(1)
            self.dds_ch_RB2.set_profile(4)
            delay(RB2_PROF_TIMES_US[4]*us)
            self.dds_ch_RB1B.set_profile(0)
            self.dds_ch_RB2.set_profile(0)
            delay(OP_TIME)
            

        self.dds_ch_RB1B.cfg_sw(False) # Enable RF switch
        self.dds_ch_RB2.cfg_sw(False) # Enable RF switch
        self.dds_ch_RB4.cfg_sw(False) # Enable RF switch


class UrukulRSCTestExperiment(ExpFragment):
    # TODO: Make a test class that calls the initialisation of urukul channels, then runs
    # the fragment defined above.
    def __init__(self):
        pass