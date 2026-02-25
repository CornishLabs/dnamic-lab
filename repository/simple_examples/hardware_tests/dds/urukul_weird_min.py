from artiq.language.units import MHz, dB, s, ms, us, V, ns
from artiq.language.core import delay, kernel, rpc, delay_mu, now_mu, at_mu
from artiq.language.environment import EnvExperiment
from artiq.coredevice.ad9910 import AD9910
from artiq.coredevice.core import Core
from artiq.coredevice.urukul import CPLD
from artiq.coredevice.ttl import TTLOut

from artiq.coredevice.ad9910 import _AD9910_REG_SYNC
from numpy import int32


"""
      110-------111
     / |       / |
    /  |      /  |
  010-------011  |

   |   |     |   |
   |  100----|--101
   | /       | /
   |/        |/
  000-------001
"""
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
    def check_print_sync_reg(self):
        reg0a = int32(self.dds_ch.read32(_AD9910_REG_SYNC))
        self.core.break_realtime()
        # Decode bits according to datasheet/ARTIQ set_sync():
        window   = (reg0a >> 28) & 0xF      # [31:28]
        rx_en    = (reg0a >> 27) & 0x1      # [27]
        gen_en   = (reg0a >> 26) & 0x1      # [26]
        gen_pol  = (reg0a >> 25) & 0x1      # [25]
        preset   = (reg0a >> 18) & 0x3F     # [23:18]
        in_delay = (reg0a >> 3)  & 0x1F     # [7:3]

        # Read CPLD status register and extract per-channel bits
        sta = self.dds_ch.cpld.sta_read()
        smp_err_all = (sta >> 4) & 0xF      # STA_SMP_ERR base bit offset = 4
        pll_lock_all = (sta >> 8) & 0xF     # STA_PLL_LOCK base bit offset = 8

        ch = self.dds_ch.chip_select - 4            # chip_select 4..7 -> channel 0..3
        smp_err = (smp_err_all >> ch) & 0x1
        pll_lock = (pll_lock_all >> ch) & 0x1

        print(reg0a, rx_en, gen_en, gen_pol, window, preset, in_delay, smp_err, pll_lock)

    @kernel
    def run(self):
        self.core.reset()
        self.core.break_realtime()
        # Initialise and setup urukul cpld, channels, and attenuators
        self.dds_cpld.init()
        self.init_dds(self.dds_ch)

        # If you haven't set the EEPROM at any point, then after a power cycle this will read
        # 0 0 0 0 0 0 0 0 1
        # You can run `artiq_sinara_tester --only urukuls` to write values to the EEPROM
        self.check_print_sync_reg()
        self.core.break_realtime()

        in_delay_func, window_func = self.dds_ch.tune_sync_delay(13) # This call also has dds_ch_index for some reason
        print(in_delay_func, window_func)
        self.core.break_realtime()

        self.check_print_sync_reg()
        self.core.break_realtime()

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
        delay_mu(2) # Phase shift?
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

