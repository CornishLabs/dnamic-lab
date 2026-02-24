from artiq.language.units import MHz, dB, s, ms, us, V, ns
from artiq.language.core import delay, kernel, rpc, delay_mu
from artiq.language.environment import EnvExperiment
from artiq.coredevice.ad9910 import AD9910
from artiq.coredevice.core import Core
from artiq.coredevice.urukul import CPLD
from artiq.coredevice.ttl import TTLOut

class UrukulWeirdExample(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        self.core: Core
        self.setattr_device("core_dma")
        self.setattr_device("ttl0")
        self.ttl0: TTLOut

        self.dds_cpld: CPLD = self.get_device("urukul6_cpld")
        self.dds_ch: AD9910 = self.get_device("urukul6_ch1")
        
        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {"dds_cpld", "dds_ch"}
    
    @kernel
    def init_dds(self, dds):
        dds.init()
        dds.set_att(6.*dB)
        dds.cfg_sw(False)

    @kernel
    def configure_RB1AB_single_tone_mode(self, dds):
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.75, profile=1)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.85, profile=2)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.5, profile=3)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.8, profile=4)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.37, profile=5)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.37, profile=6)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.37, profile=7)

        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.0, profile=0) # Off
        delay(10*us) #Give some time for io update after
        dds.set_profile(0) # Set profile pins to default of 0. 
        # This should act like an IO_update, but given issue, also...
        # ... pulse IO_Update (this will pulse on all 4x AD9910 channels unfortunately) 
        dds.io_update.pulse_mu(8)
        delay(20*us)


    @kernel
    def record(self):
        with self.core_dma.record("pulses"):
            # all RTIO operations now_mu go to the "pulses"
            # DMA buffer, instead of being executed immediately.
            for i in range(50):
                # Axial n-4
                self.dds_ch.set_profile(6)  # 6 = 110_b
                delay(40*us)
                self.dds_ch.set_profile(0)  # 0 = 000_b
                delay(15*us)

                # I expect here we could accidently be in P2=010_b or P4=100_b
                            
                # Radial x
                self.dds_ch.set_profile(3)  # 3 = 011_b,
                delay(40*us)
                self.dds_ch.set_profile(0)
                delay(15*us)
    
    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        self.core.break_realtime()
        # Initialise and setup urukul cpld, channels, and attenuators
        at_mu(now_mu() & ~7)
        delay(2*ns)
        self.dds_cpld.init()
        self.init_dds(self.dds_ch)

        # Setup single tone mode
        self.configure_RB1AB_single_tone_mode(self.dds_ch)

        self.core.break_realtime()
        # Profile playback
        # Note that set_profile actually advances the timeline
        # (by 0.86us when I measured it) so the delays for direct profile switches
        # are not necessarily the delays you expect. Therefore be careful when trying
        # to do a preciscely timed square pulse with the RAM_MODE_DIRECTSWITCH mode.
        # This is because the configuration register of the CPLD is written over SPI
        # Which *then* changes the profile pins internally.

        self.dds_ch.cfg_sw(True) # Enable RF switch

        # Lock cursor to a 4ns (coarse RTIO) timestamps (rounded down)
        at_mu(now_mu() & ~7)
        delay(6*ns) # Phase shift?
        # Now everything must be done on a 4ns grid.
        # SPI set_profile is 860ns/4 = 215 so that's fine.
        # You just have to make it so that the delays are also multiples of 4ns

        self.ttl0.pulse(1*us) # scope trigger
        
        self.record()
        pulses_handle = self.core_dma.get_handle("pulses")
        self.core_dma.playback_handle(pulses_handle)

        # for i in range(50):
        #     # Axial n-4
        #     self.dds_ch.set_profile(6)  # 6 = 110_b
        #     delay(40*us)
        #     self.dds_ch.set_profile(0)  # 0 = 000_b
        #     delay(15*us)

        #     # I expect here we could accidently be in P2=010_b or P4=100_b
                        
        #     # Radial x
        #     self.dds_ch.set_profile(3)  # 3 = 011_b,
        #     delay(40*us)
        #     self.dds_ch.set_profile(0)
        #     delay(15*us)

        #     # I expect here we could accidently be in P2=010_b or P1=001_b

        self.dds_ch.cfg_sw(False) 


"""
Hi, I have been using the ProtoRev9 Urukul to perform a complicated pulse sequence by changing the profile pins on each of the AD9910's in an Urukul card (new ProtoRev9 Feature). While programming this sequence, I kept experiencing unexpected tones appearing on my scope after changing profile pins while on a satellite urukul.

I believe I have narrowed this down to the effects of intermediate profiles on the Hamming path activating, although I am not certain the mechanism beyond that as I am not certain how the SPI transfer and subsequent activation of the profile pins occurs at the CPLD level. I would expect that the CPLD would simultaneously change the state of all the profile pins at least simultaneously? Although I don't know how the 'clock domains' if that's the right word cross between the SPI bus, CPLD, and the urukul.

It appears to be non-deterministic on two levels

    When changing with from profile x to profile y with set_profile(y) , sometimes the hamming path intermediate between x and y latches on and stays on, with some given probability (see below). E.g. if changing from profile 6 = 110_b to profile 0=000_b, I sometimes end up playing profile 2=010_b or profile 4=100_b.
    When turning off and on the crate with the urukul in, the amount of randomness appears to be random. Some power on cycles I see no errors, others I see lots.

This to me, an uninitiated fellow, appears to be some random clock synchronisation issue that sometimes lines up with the chip reading the profile pins on an unstable edge. However locking to the coarse RTIO clock with at_mu(now_mu() & ~7) doesn't work and this issue still appears. Perhaps the SPI core is aligned to the coarse RTIO clock (125MHz), but the urukul SYNC_CLK = SYS_CLK/4 (Also 125MHz, derived from the core clock through the MMCX) has some random phase that sometimes makes the SPI writing to the register while it's being read?

I have seen this behaviour on urukuls in the master crate, and in the satellite crate, although (purely based on an empirical data) that I have noticed it be more problematic on the satellite. This could be confirmation bias from using the satellite and power cycling it more to test.

Does anyone more acquainted with the Urukul control have any ideas? Further things I could test?
Thanks for your help in advance 🙂 .

Here is a fairly minimal 'working' example:
(set scope to 1ms/div)
It should look like (smallamp-off-bigamp-off)50 but sometimes randomly looks broken.
See below screenshots

from artiq.language.units import MHz, dB, s, ms, us, V, ns
from artiq.language.core import delay, kernel, rpc, delay_mu
from artiq.language.environment import EnvExperiment
from artiq.coredevice.ad9910 import AD9910
from artiq.coredevice.core import Core
from artiq.coredevice.urukul import CPLD
from artiq.coredevice.ttl import TTLOut

class UrukulWeirdExample(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        self.core: Core
        self.setattr_device("ttl0")
        self.ttl0: TTLOut

        self.dds_cpld: CPLD = self.get_device("urukul6_cpld")
        self.dds_ch: AD9910 = self.get_device("urukul6_ch1")
        
        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {"dds_cpld", "dds_ch"}
    
    @kernel
    def init_dds(self, dds):
        dds.init()
        dds.set_att(6.*dB)
        dds.cfg_sw(False)

    @kernel
    def configure_RB1AB_single_tone_mode(self, dds):
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.75, profile=1)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.85, profile=2)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.5, profile=3)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.8, profile=4)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.37, profile=5)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.37, profile=6)
        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.37, profile=7)

        dds.set(frequency=2*MHz, phase=0.0, amplitude=0.0, profile=0) # Off
        delay(10*us) #Give some time for io update after
        dds.set_profile(0) # Set profile pins to default of 0. 
        # This should act like an IO_update, but given issue, also...
        # ... pulse IO_Update (this will pulse on all 4x AD9910 channels unfortunately) 
        dds.io_update.pulse_mu(8)
        delay(20*us)
        
    
    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        # Initialise and setup urukul cpld, channels, and attenuators
        self.dds_cpld.init()
        self.init_dds(self.dds_ch)

        # Setup single tone mode
        self.configure_RB1AB_single_tone_mode(self.dds_ch)

        self.core.break_realtime()
        # Profile playback
        # Note that set_profile actually advances the timeline
        # (by 0.86us when I measured it) so the delays for direct profile switches
        # are not necessarily the delays you expect. Therefore be careful when trying
        # to do a preciscely timed square pulse with the RAM_MODE_DIRECTSWITCH mode.
        # This is because the configuration register of the CPLD is written over SPI
        # Which *then* changes the profile pins internally.

        self.dds_ch.cfg_sw(True) # Enable RF switch

        # Lock cursor to a 4ns (coarse RTIO) timestamps (rounded down)
        at_mu(now_mu() & ~7)
        #delay(2*ns) # Phase shift?
        # Now everything must be done on a 4ns grid.
        # SPI set_profile is 860ns/4 = 215 so that's fine.
        # You just have to make it so that the delays are also multiples of 4ns

        self.ttl0.pulse(1*us) # scope trigger

        for i in range(50):
            # Axial n-4
            self.dds_ch.set_profile(6)  # 6 = 110_b
            delay(40*us)
            self.dds_ch.set_profile(0)  # 0 = 000_b
            delay(15*us)

            # I expect here we could accidently be in P2=010_b or P4=100_b
                        
            # Radial x
            self.dds_ch.set_profile(3)  # 3 = 011_b,
            delay(40*us)
            self.dds_ch.set_profile(0)
            delay(15*us)

            # I expect here we could accidently be in P2=010_b or P1=001_b

        self.dds_ch.cfg_sw(False) 

Intended behavior (power cycled until it looked good):

After power cycling the satellite crate a few times until the condition occurs (took approx 6 power cycles):

Using artiq-9 master, and the latest ProtoRev9 CPLD firmware.

Edit, Related:
https://ez.analog.com/data_converters/dds/f/q-a/566078/ad9910-profile-pin-issue
https://ez.analog.com/data_converters/dds/f/q-a/548761/ad9910-eval-board-profile-pin-issues/432014
https://github.com/m-labs/artiq/issues/1994
"""