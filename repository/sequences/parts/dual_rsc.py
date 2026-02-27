"""
This module contains fragments for performing dual species Raman Sideband Cooling (RSC) of atoms
in tweezer traps.

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

TODO LIST:
Setup RB1A/B in sequence rather than device setup
Keep RB2/4 in device setup (and keep note)
"""

import logging
import numpy as np
from dataclasses import dataclass

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

from ndscan.experiment import Fragment, ExpFragment, make_fragment_scan_exp
from ndscan.experiment.parameters import IntParam, FloatParam

from repository.sequences.parts.initialiser import InitialiseHardware

logger = logging.getLogger(__name__)

# ---------- logical pulse (multiple lasers come on) definitions ----------
@dataclass(frozen=True)
class PulseDef:
    rb1ab: str = "off"
    rb2: str = "off"
    rb4: str = "off"
    duration_src: tuple[str, str] = ("rb2", "off")   # ("rb2" or "rb4", key)

# TODO: Could combine RAMProfData & RAMProfileLayout
@dataclass(frozen=True)
class RAMProfData:
    steps_per_cycle: int = 1

@dataclass(frozen=True)
class RAMProfileLayout:
    region: str   # "off", "on", "shape"
    mode: int

@dataclass(frozen=True)
class RAMModeData:
    amp_scale: float = 0.5
    frequency_mhz: float = 110.0

@dataclass(frozen=True)
class SingleToneProfData:
    amplitude: float = 0.5
    frequency_mhz: float = 100.0
    phase: float = 0.0



# Mapping logical names to hardware profiles
RB1AB_PROFILE = {
    "off": 0,
    "R2": 1,
    "R1": 2, 
    "Zm1": 3,
    "Zm2": 4,
    "Zm3": 5,
    "Zm4": 6,
    "RbFlip": 7,
} #Both RB1A and RB1B share this mapping.
RB1A_RADIAL_AMP_DEFAULT = 0.735
RB1A_AXIAL_AMP_DEFAULT = 0.11
DEFAULT_SHIFT_MHZ = 95 # For testing
RB1A_PROFILE_DEFAULT_INFO = {
    "off":    SingleToneProfData(0.0,120.0,0.0),
    "R2":     SingleToneProfData(RB1A_RADIAL_AMP_DEFAULT, 100.110-DEFAULT_SHIFT_MHZ,  0.0),
    "R1":     SingleToneProfData(RB1A_RADIAL_AMP_DEFAULT, 100.083-DEFAULT_SHIFT_MHZ,  0.0), 
    "Zm1":    SingleToneProfData(RB1A_AXIAL_AMP_DEFAULT,  100.057-DEFAULT_SHIFT_MHZ,  0.0),
    "Zm2":    SingleToneProfData(RB1A_AXIAL_AMP_DEFAULT,  100.075-DEFAULT_SHIFT_MHZ,  0.0),
    "Zm3":    SingleToneProfData(RB1A_AXIAL_AMP_DEFAULT,  100.094-DEFAULT_SHIFT_MHZ,  0.0),
    "Zm4":    SingleToneProfData(RB1A_AXIAL_AMP_DEFAULT,  100.112-DEFAULT_SHIFT_MHZ,  0.0),
    "RbFlip": SingleToneProfData(RB1A_RADIAL_AMP_DEFAULT,  99.991-DEFAULT_SHIFT_MHZ,  0.0),
}

RB1B_RADIAL_AMP_DEFAULT = 0.49
RB1B_AXIAL_AMP_DEFAULT = 0.183
RB1B_PROFILE_DEFAULT_INFO = {
    "off":    SingleToneProfData(0.0,120.0,0.0),
    "R2":     SingleToneProfData(RB1B_RADIAL_AMP_DEFAULT, 100.051-DEFAULT_SHIFT_MHZ,  0.0),
    "R1":     SingleToneProfData(RB1B_RADIAL_AMP_DEFAULT, 100.023-DEFAULT_SHIFT_MHZ,  0.0), 
    "Zm1":    SingleToneProfData(RB1B_AXIAL_AMP_DEFAULT,   99.977-DEFAULT_SHIFT_MHZ,  0.0),
    "Zm2":    SingleToneProfData(RB1B_AXIAL_AMP_DEFAULT,   99.996-DEFAULT_SHIFT_MHZ,  0.0),
    "Zm3":    SingleToneProfData(RB1B_AXIAL_AMP_DEFAULT,  100.015-DEFAULT_SHIFT_MHZ,  0.0),
    "Zm4":    SingleToneProfData(RB1B_AXIAL_AMP_DEFAULT,  100.034-DEFAULT_SHIFT_MHZ,  0.0),
    "RbFlip": SingleToneProfData(RB1B_RADIAL_AMP_DEFAULT,  99.920-DEFAULT_SHIFT_MHZ,  0.0),
}

### RB2 ###
RB2_PROFILE = {
    "off": 0,
    "R1": 1,
    "R1longer": 2,
    "unused3": 3,
    "R2": 4,
    "unused5": 5,
    "RbFlip": 6,
    "CsFlip": 7,
}

RB2_RAM_SETTINGS_DEFAULT_INFO = RAMModeData(0.47,110.0)

RB2_PROFILE_DEFAULT_INFO = {
    "off": RAMProfData(1),
    "R1": RAMProfData(42),
    "R1longer": RAMProfData(62),
    "unused3": RAMProfData(92),
    "R2": RAMProfData(50),
    "unused5": RAMProfData(1),
    "RbFlip": RAMProfData(1),
    "CsFlip": RAMProfData(1),
}

RB2_RAM_LAYOUT = {
    "off":      RAMProfileLayout("off",   RAM_MODE_DIRECTSWITCH),
    "R1":       RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "R1longer": RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "unused3":  RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "R2":       RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "unused5":  RAMProfileLayout("on",    RAM_MODE_DIRECTSWITCH),
    "RbFlip":   RAMProfileLayout("on",    RAM_MODE_DIRECTSWITCH),
    "CsFlip":   RAMProfileLayout("on",    RAM_MODE_DIRECTSWITCH),
}

### RB4 ###
RB4_PROFILE = {
    "off": 0,
    "unused1": 1,
    "Zm1weird": 2,
    "Zm1": 3,
    "Zm2": 4,
    "Zm3": 5,
    "Zm4": 6,
    "unused7": 7,
}

RB4_RAM_SETTINGS_DEFAULT_INFO = RAMModeData(0.8,110.0)

RB4_PROFILE_DEFAULT_INFO = {
    "off": RAMProfData(1),
    "unused1": RAMProfData(142),
    "Zm1weird": RAMProfData(215),
    "Zm1": RAMProfData(175),
    "Zm2": RAMProfData(210),
    "Zm3": RAMProfData(158),
    "Zm4": RAMProfData(170),
    "unused7": RAMProfData(312),
}

RB4_RAM_LAYOUT = {
    "off":      RAMProfileLayout("off",   RAM_MODE_DIRECTSWITCH),
    "unused1":  RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "Zm1weird": RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "Zm1":      RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "Zm2":      RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "Zm3":      RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "Zm4":      RAMProfileLayout("shape", RAM_MODE_RAMPUP),
    "unused7":  RAMProfileLayout("shape", RAM_MODE_RAMPUP),
}

## Pulses ##
PULSES = {
    "AX_ZM4":       PulseDef(rb1ab="Zm4", rb4="Zm4",       duration_src=("rb4", "Zm4")),
    "AX_ZM3":       PulseDef(rb1ab="Zm3", rb4="Zm3",       duration_src=("rb4", "Zm3")),
    "AX_ZM2":       PulseDef(rb1ab="Zm2", rb4="Zm2",       duration_src=("rb4", "Zm2")),
    "AX_ZM1":       PulseDef(rb1ab="Zm1", rb4="Zm1",       duration_src=("rb4", "Zm1")),
    "AX_ZM1_WEIRD": PulseDef(rb1ab="Zm1", rb4="Zm1weird",  duration_src=("rb4", "Zm1weird")),
    "RAD_1":        PulseDef(rb1ab="R1",  rb2="R1",        duration_src=("rb2", "R1")),
    "RAD_1_LONG":   PulseDef(rb1ab="R1",  rb2="R1longer",  duration_src=("rb2", "R1longer")),
    "RAD_2":        PulseDef(rb1ab="R2",  rb2="R2",        duration_src=("rb2", "R2")),
}

SEQUENCE_BLOCKS = [
    (6,  ("AX_ZM4", "RAD_1",      "AX_ZM4", "RAD_2")),
    (10, ("AX_ZM3", "RAD_1",      "AX_ZM3", "RAD_2")),
    (10, ("AX_ZM2", "RAD_1",      "AX_ZM2", "RAD_2")),
    (15, ("AX_ZM2", "RAD_1_LONG", "AX_ZM1", "RAD_2",
          "AX_ZM1_WEIRD", "RAD_1",
          "AX_ZM2", "RAD_1_LONG", "AX_ZM1", "RAD_2")),
]

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

        # logger.info("Test0")

        ## OP settings
        self.setattr_param('OP_time',
                           FloatParam,
                           "How long to OP between Raman pulses (us)",
                           default=OP_TIME_US*us, min=1.0*us, max=200.0*us,
                           unit='us',
                        )
        
        self.setattr_param('OP12_frequency',
            FloatParam,
            "Frequency played for OP goes to AOM",
            default=OP12_FREQ_MHZ*MHz, min=1.0*MHz, max=200.0*MHz,
            unit='MHz',
        )
        
        self.setattr_param('OP22_frequency',
            FloatParam,
            "Frequency played for OP goes to AOM",
            default=OP22_FREQ_MHZ*MHz, min=1.0*MHz, max=200.0*MHz,
            unit='MHz'
        )

        self.setattr_param('OP34_frequency',
            FloatParam,
            "Frequency played for OP goes to AOM",
            default=OP34_FREQ_MHZ*MHz, min=1.0*MHz, max=200.0*MHz,
            unit='MHz'
        )
        
        self.setattr_param('OP44_frequency',
            FloatParam,
            "Frequency played for OP goes to AOM",
            default=OP44_FREQ_MHZ*MHz, min=1.0*MHz, max=200.0*MHz,
            unit='MHz'
        )

        self.setattr_param('OP12_amp',
            FloatParam,
            "Amp relative to full scale (0-1) to AOM",
            default=OP12_AMP, min=0.0, max=1.0
        )
        
        self.setattr_param('OP22_amp',
            FloatParam,
            "Amp relative to full scale (0-1) to AOM",
            default=OP22_AMP, min=0.0, max=1.0
        )

        self.setattr_param('OP34_amp',
            FloatParam,
            "Amp relative to full scale (0-1) to AOM",
            default=OP34_AMP, min=0.0, max=1.0
        )
        
        self.setattr_param('OP44_amp',
            FloatParam,
            "Amp relative to full scale (0-1) to AOM",
            default=OP44_AMP, min=0.0, max=1.0
        )
        
        ## Single tone RSC beams
        ### RB1A
        self.RB1A_frequency_param_handles = {}
        self.RB1A_amp_param_handles = {}
        for key, default in RB1A_PROFILE_DEFAULT_INFO.items():
            
            name = f"RB1A_{key}_frequency"
            param_handle = self.setattr_param(name, FloatParam,
                 f"RB1A {key} AOM drive frequency",
                default=default.frequency_mhz*MHz, min=1*MHz, max=200*MHz,
                unit='MHz')
            self.RB1A_frequency_param_handles[key] = param_handle
            
            name = f"RB1A_{key}_amp"
            param_handle= self.setattr_param(name, 
                FloatParam,
                f"RB1A {key} Amp (0-1, relative to full scale)",
                default=default.amplitude, min=0.0, max=1.0
            )
            self.RB1A_amp_param_handles[key] = param_handle

        ### RB1B
        self.RB1B_frequency_param_handles = {}
        self.RB1B_amp_param_handles = {}
        for key, default in RB1B_PROFILE_DEFAULT_INFO.items():
            
            name = f"RB1B_{key}_frequency"
            param_handle = self.setattr_param(name, FloatParam,
                 "Frequency played for profile goes to AOM",
                default=default.frequency_mhz*MHz, min=1*MHz, max=200*MHz,
                unit='MHz')
            self.RB1B_frequency_param_handles[key] = param_handle
            
            name = f"RB1B_{key}_amp"
            param_handle= self.setattr_param(name, 
                FloatParam,
                "Amp played for profile (0-1, relative to full scale)",
                default=default.amplitude, min=0.0, max=1.0
            )
            self.RB1B_amp_param_handles[key] = param_handle

        ## RAM mode RSC beams
        ### RB2
        self.RB2_cps_param_handles = {}
        for key, default in RB2_PROFILE_DEFAULT_INFO.items():
            
            name = f"RB2_{key}_cycles_per_step"
            param_handle = self.setattr_param(name, IntParam,
                "How many cycles of SYNC_CLK (normally 4ns/cycle) before changing RAM step",
                default=default.steps_per_cycle, min=1, max=65534)
            self.RB2_cps_param_handles[key] = param_handle
            
        self.setattr_param('RB2_amp_scale', 
                    FloatParam, "Scale RAM pulse by this factor (scaling an envelope with a peak of 1)",
                    default=RB2_RAM_SETTINGS_DEFAULT_INFO.amp_scale, min=0.0, max=1.0
                    )
        
        ### RB4
        self.RB4_cps_param_handles = {}
        for key, default in RB4_PROFILE_DEFAULT_INFO.items():
            
            name = f"RB4_{key}_cycles_per_step"
            param_handle = self.setattr_param(name, IntParam,
                "How many cycles of SYNC_CLK (normally 4ns/cycle) before changing RAM step",
                default=default.steps_per_cycle, min=1, max=65534)
            self.RB4_cps_param_handles[key] = param_handle
            
        self.setattr_param('RB4_amp_scale', 
                    FloatParam, "Scale RAM pulse by this factor (scaling an envelope with a peak of 1)",
                    default=RB4_RAM_SETTINGS_DEFAULT_INFO.amp_scale, min=0.0, max=1.0
                    )
        
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
        self.dds_ch_rb_op22: AD9910 = self.get_device("dds_ch_rb_op22")
        self.dds_ch_rb_op12: AD9910 = self.get_device("dds_ch_rb_op12")
        self.dds_ch_cs_op44: AD9910 = self.get_device("dds_ch_cs_op44")
        self.dds_ch_cs_op34: AD9910 = self.get_device("dds_ch_cs_op34")
 
        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {"dds_cpld_rsc", "dds_ch_RB1A", "dds_ch_RB1B", "dds_ch_RB2", "dds_ch_RB4", "dds_ch_rb_op22", "dds_ch_rb_op12", "dds_ch_cs_op44", "dds_ch_cs_op34"}

    def _ceil8_mu(self, mu: int) -> int:
        return (mu + 7) & ~7

    def _ram_duration_mu(self, cycles_per_step: int, n_steps: int) -> int:
        # 4 ns * cycles_per_step * n_steps
        mu = self.core.seconds_to_mu(cycles_per_step * 4e-9 * n_steps)
        return self._ceil8_mu(mu)
    
    def _compile_sequence(self):
        
        # Work out how long the RAM pulses will take beforehand
        RB2_dur_mu = {}
        for key, param_handle in self.RB2_cps_param_handles.items():
            RB2_dur_mu[key] = self._ram_duration_mu(param_handle.get(), self.tukey_steps)

        RB4_dur_mu = {}
        for key, param_handle in self.RB4_cps_param_handles.items():
            RB4_dur_mu[key] = self._ram_duration_mu(param_handle.get(), self.bh_steps)


        # flatten the logical sequence into plain integer arrays (profile numbers)
        seq_rb1ab = []
        seq_rb2 = []
        seq_rb4 = []
        seq_dur_mu = []

        for nreps, block in SEQUENCE_BLOCKS:
            for _ in range(nreps):
                for pulse_name in block:
                    p = PULSES[pulse_name]

                    # What profile each chip should be in per pulse
                    seq_rb1ab.append(RB1AB_PROFILE[p.rb1ab])
                    seq_rb2.append(RB2_PROFILE[p.rb2])
                    seq_rb4.append(RB4_PROFILE[p.rb4])

                    # How long the pulse will take
                    src_kind, src_key = p.duration_src
                    if src_kind == "rb2":
                        seq_dur_mu.append(RB2_dur_mu[src_key])
                    else:
                        seq_dur_mu.append(RB4_dur_mu[src_key])

        self.seq_rb1ab = seq_rb1ab
        self.seq_rb2 = seq_rb2
        self.seq_rb4 = seq_rb4
        self.seq_dur_mu = seq_dur_mu
        self.seq_len = len(seq_dur_mu)

        self.op_time_mu = self._ceil8_mu(self.core.seconds_to_mu(self.OP_time.get()))


    def _compute_full_scale_RAM_profiles(self):
        # Convention is that all RAM profiles are:
        # off, on, [BH] in logical space.

        # Prepare pulse shape RAM for RB2 (radial -> Tukey pulse)
        
        self.tukey_steps = 400
        alpha = 0.5  # Tukey shape: 0=rectangular, 1=Hann

        self.amp_logical_rb2 = [0.0,0.7] # Useful for direct switch square pulses
        
        tk = []
        tk_scale_factor = self.RB2_amp_scale.get()
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

            tk.append(w*tk_scale_factor)

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
        bh_scale_factor = self.RB4_amp_scale.get()
        for n in range(self.bh_steps):
            x = n/(self.bh_steps-1)
            w = a0 - a1*np.cos(twopi*x) + a2*np.cos(2*twopi*x) - a3*np.cos(3*twopi*x)
            bh.append(w*bh_scale_factor)

        self.amp_logical_rb4 += bh
        
        self.asf_ram_rb4 = [0] * len(self.amp_logical_rb4) # Create array to put RAM words into
        self.amp_length_rb4 = len(self.amp_logical_rb4)
        self.amp_reversed_rb4 = list(reversed(self.amp_logical_rb4)) # Create array in expected order for chip (reversed)

    def _compile_rb1ab_program(self):
        items = sorted(RB1AB_PROFILE.items(), key=lambda kv: kv[1])

        self.rb1ab_profiles = []
        self.rb1a_freqs = []
        self.rb1a_amps = []
        self.rb1b_freqs = []
        self.rb1b_amps = []

        for key, profile in items:
            self.rb1ab_profiles.append(profile)
            self.rb1a_freqs.append(self.RB1A_frequency_param_handles[key].get())
            self.rb1a_amps.append(self.RB1A_amp_param_handles[key].get())
            self.rb1b_freqs.append(self.RB1B_frequency_param_handles[key].get())
            self.rb1b_amps.append(self.RB1B_amp_param_handles[key].get())

        self.rb1ab_profile_count = len(self.rb1ab_profiles)

    def _compile_ram_program(self, profile_map, layout_map, cps_handles, n_steps):
        profiles, starts, ends, steps, modes = [], [], [], [], []
        shape_start = 2
        shape_end = 2 + n_steps - 1

        for key, profile in sorted(profile_map.items(), key=lambda kv: kv[1]):
            layout = layout_map[key]
            if layout.region == "off":
                start, end, step = 0, 0, 1
            elif layout.region == "on":
                start, end, step = 1, 1, 1
            elif layout.region == "shape":
                start, end, step = shape_start, shape_end, cps_handles[key].get()
            else:
                raise ValueError(f"Unknown RAM region {layout.region!r}")

            profiles.append(profile)
            starts.append(start)
            ends.append(end)
            steps.append(step)
            modes.append(layout.mode)

        return profiles, starts, ends, steps, modes

    def host_setup(self):
        super().host_setup()

        # logger.info("Test1")

        # Lazy prepare (could be in prepare instead)
        self._compile_rb1ab_program()

        self._compute_full_scale_RAM_profiles()

        (self.rb2_ram_profiles,
        self.rb2_ram_starts,
        self.rb2_ram_ends,
        self.rb2_ram_steps,
        self.rb2_ram_modes) = self._compile_ram_program(
            RB2_PROFILE, RB2_RAM_LAYOUT, self.RB2_cps_param_handles, self.tukey_steps
        )
        self.rb2_ram_count = len(self.rb2_ram_profiles)

        (self.rb4_ram_profiles,
        self.rb4_ram_starts,
        self.rb4_ram_ends,
        self.rb4_ram_steps,
        self.rb4_ram_modes) = self._compile_ram_program(
            RB4_PROFILE, RB4_RAM_LAYOUT, self.RB4_cps_param_handles, self.bh_steps
        )
        self.rb4_ram_count = len(self.rb4_ram_profiles)
        
        self._compile_sequence()

    @kernel
    def configure_RB1AB_single_tone_mode(self):
        delay(4*us)
        self.dds_ch_RB1A.set_profile(0)
        self.dds_ch_RB1B.set_profile(0)
        delay(4*us)

        for i in range(self.rb1ab_profile_count):
            profile = self.rb1ab_profiles[i]

            self.dds_ch_RB1A.set(
                frequency=self.rb1a_freqs[i],
                phase=0.0,
                amplitude=self.rb1a_amps[i],
                profile=profile
            )

            self.dds_ch_RB1B.set(
                frequency=self.rb1b_freqs[i],
                phase=0.0,
                amplitude=self.rb1b_amps[i],
                profile=profile
            )

    @kernel
    def _apply_rb2_ram_program(self, rb2_dds):
        for i in range(self.rb2_ram_count):
            rb2_dds.set_profile_ram(
                start=self.rb2_ram_starts[i],
                end=self.rb2_ram_ends[i],
                step=self.rb2_ram_steps[i],
                profile=self.rb2_ram_profiles[i],
                mode=self.rb2_ram_modes[i]
            )
            rb2_dds.io_update.pulse_mu(8)

    @kernel
    def _apply_rb4_ram_program(self, rb4_dds):
        for i in range(self.rb4_ram_count):
            rb4_dds.set_profile_ram(
                start=self.rb4_ram_starts[i],
                end=self.rb4_ram_ends[i],
                step=self.rb4_ram_steps[i],
                profile=self.rb4_ram_profiles[i],
                mode=self.rb4_ram_modes[i]
            )
            rb4_dds.io_update.pulse_mu(8)


    @kernel
    def configure_RB24_ram_mode(self):
        rb2_dds, rb4_dds = self.dds_ch_RB2,self.dds_ch_RB4
        ##### RB2
        # Disable ram mode while setting up
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
        self._apply_rb2_ram_program(rb2_dds)
        
        rb2_dds.set(frequency=110*MHz, ram_destination=RAM_DEST_ASF)   # Set what the frequency is, and what the RAM does (ASF)
        rb2_dds.set_cfr1(ram_enable=1, ram_destination=RAM_DEST_ASF) # Enable RAM, Pass osk_enable=1 to set_cfr1() if it is not an amplitude RAM
        rb2_dds.io_update.pulse_mu(8) # Write to CPLD

        ##### RB4

        rb4_dds.set_cfr1(ram_enable=0) # Control Function Register 1
        rb4_dds.io_update.pulse_mu(8)

        # 1) Loader profile (also the initial profile we start in)
        # This loads all the RAM at once 

        # 2) Program the whole RAM all at once using LOADER profile 
        self.core.break_realtime()
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
        
        # 3) Set profile control registers
        self._apply_rb4_ram_program(rb4_dds)
        
        
        rb4_dds.set(frequency=110*MHz, ram_destination=RAM_DEST_ASF) # Set what the frequency is, and what the RAM does (ASF)
        rb4_dds.set_cfr1(ram_enable=1, ram_destination=RAM_DEST_ASF) # Enable RAM, Pass osk_enable=1 to set_cfr1() if it is not an amplitude RAM
        rb4_dds.io_update.pulse_mu(8) # Write to CPLD



    @kernel
    def configure_OP_beams(self):

        self.dds_ch_rb_op12.set_profile(1)
        self.dds_ch_cs_op34.set_profile(1)
        self.dds_ch_rb_op22.set_profile(1)
        self.dds_ch_cs_op44.set_profile(1)

        # Profile 0 is off
        self.dds_ch_rb_op12.set(frequency=100*MHz, phase=0.0, amplitude=0.0, profile=0)
        self.dds_ch_cs_op34.set(frequency=100*MHz, phase=0.0, amplitude=0.0, profile=0)
        self.dds_ch_rb_op22.set(frequency=180*MHz, phase=0.0, amplitude=0.0, profile=0)
        self.dds_ch_cs_op44.set(frequency=180*MHz, phase=0.0, amplitude=0.0, profile=0)

        # Profile 1 is on
        self.dds_ch_rb_op12.set(frequency=self.OP12_frequency.get(), phase=0.0, amplitude=self.OP12_amp.get(), profile=1)
        self.dds_ch_cs_op34.set(frequency=self.OP34_frequency.get(), phase=0.0, amplitude=self.OP34_amp.get(), profile=1)
        self.dds_ch_rb_op22.set(frequency=self.OP22_frequency.get(), phase=0.0, amplitude=self.OP22_amp.get(), profile=1)
        self.dds_ch_cs_op44.set(frequency=self.OP44_frequency.get(), phase=0.0, amplitude=self.OP44_amp.get(), profile=1)



    @kernel
    def device_setup(self):
        self.core.break_realtime()
        # self.device_setup_subfragments() # Should be NO-OP for this fragment

        # TODO: Make this do nothing if no params changed
                
        # Setup DDS RAM mode, upload RAM, and set profile registers
        delay(10*us)
        self.configure_RB24_ram_mode()
        delay(10*us)


    @kernel
    def play_one_pulse(self, rb1ab_prof, rb2_prof, rb4_prof, dur_mu, op_mu):
        """
        Plays a Raman pulse first, then an optical pumping pulse.
        """

        # Raman pulse
        if rb1ab_prof != 0:
            self.dds_ch_RB1A.sw.on()
            self.dds_ch_RB1B.sw.on()
            self.dds_ch_RB1A.set_profile(rb1ab_prof)
            self.dds_ch_RB1B.set_profile(rb1ab_prof)
        if rb2_prof != 0:
            self.dds_ch_RB2.sw.on()
            self.dds_ch_RB2.set_profile(rb2_prof)
        if rb4_prof != 0:
            self.dds_ch_RB4.sw.on()
            self.dds_ch_RB4.set_profile(rb4_prof)

        delay_mu(dur_mu)

        if rb1ab_prof != 0:
            self.dds_ch_RB1A.sw.off()
            self.dds_ch_RB1B.sw.off()
            self.dds_ch_RB1A.set_profile(0)
            self.dds_ch_RB1B.set_profile(0)
        if rb2_prof != 0:
            self.dds_ch_RB2.sw.off()
            self.dds_ch_RB2.set_profile(0)
        if rb4_prof != 0:
            self.dds_ch_RB4.sw.off()
            self.dds_ch_RB4.set_profile(0)

        # OP Pulse
        self.dds_ch_cs_op34.sw.on()
        self.dds_ch_cs_op44.sw.on()
        self.dds_ch_rb_op12.sw.on()
        self.dds_ch_rb_op22.sw.on()
        delay_mu(op_mu)
        self.dds_ch_cs_op34.sw.off()
        self.dds_ch_cs_op44.sw.off()
        self.dds_ch_rb_op12.sw.off()
        self.dds_ch_rb_op22.sw.off()
    
    @kernel
    def play_rsc_pulses(self):
        """
        Assumptions:
        - DDS CPLD, DDS Chs (AD9910s) are initialised, and that they have valid SYNC_CLK w.r.t RTIO
            (see https://forum.m-labs.hk/d/1221-urukul-pr9-set-profile-non-deterministic-intermediate-hamming-path-activation)
        - Profile settings (Profile settings, single tone/ram mode setup) are valid NOW. 
            This fragment sets them in device_setup() for RB2/4. But this means caution must be 
            applied if this method wanted to be called multiple times with different settings for
            RB2/4 in each sequence, as the last fragment set in build_fragment() will take precedence.
        """

        # Profile playback
        # Note that set_profile actually advances the timeline
        # (by 0.86us when I measured it) so the delays for direct profile switches
        # are not necessarily the delays you expect. Therefore be careful when trying
        # to do a preciscely timed square pulse with the RAM_MODE_DIRECTSWITCH mode.
        # This is because the configuration register of the CPLD is written over SPI
        # Which *then* changes the profile pins internally.

        # Configure OP beams
        self.core.break_realtime()
        self.configure_OP_beams()

        # Configure RB1A/B
        self.core.break_realtime()
        self.configure_RB1AB_single_tone_mode()
        
        # Start pulse sequence
        self.core.break_realtime()
        self.dds_ch_RB1A.sw.on()
        self.dds_ch_RB1B.sw.on()
        self.dds_ch_RB2.sw.on() 
        self.dds_ch_RB4.sw.on() 
        
        for i in range(self.seq_len):
            self.play_one_pulse(
                self.seq_rb1ab[i],
                self.seq_rb2[i],
                self.seq_rb4[i],
                self.seq_dur_mu[i],
                self.op_time_mu
            )
        
        self.dds_ch_RB1A.sw.off()
        self.dds_ch_RB1B.sw.off() 
        self.dds_ch_RB2.sw.off() 
        self.dds_ch_RB4.sw.off() 


class UrukulRSCTest(ExpFragment):

    def build_fragment(self):
        self.setattr_device("core")
        self.core: Core
        self.setattr_fragment("initialiser", InitialiseHardware)
        self.initialiser: InitialiseHardware
        self.setattr_fragment("rsc", UrukulRSCExample)
        self.rsc: UrukulRSCExample

    @kernel
    def run_once(self):
        self.core.break_realtime()
        self.initialiser.safe_off()
        self.core.break_realtime()    
        self.rsc.play_rsc_pulses()
        # # delay(5*ms)
        # self.rsc.configure_RB24_ram_mode()
        # self.core.break_realtime()
        # delay(5*ms)
        # self.rsc.play_rsc_pulses()
        # logger.info("Test2")

UrukulRSCTestExperiment = make_fragment_scan_exp(UrukulRSCTest)