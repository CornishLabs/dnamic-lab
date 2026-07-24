"""RAM modulation of the held Cs tweezer DDS amplitude.

The ordinary Cs intensity servo writes AD9910 single-tone profile 7.  This part
temporarily freezes that servo, uses profile 7 as a constant-amplitude bridge into
RAM mode, plays a looping sine from profile 6, and then restores the closed servo.

The experimental parameters live here rather than in ``LabRTIOHardware``: frequency,
depth, and duration describe a piece of a shot, not the physical apparatus.  The part
still borrows the one globally owned hardware instance and never initialises it.
"""

from math import pi, sin

from numpy import int64

from artiq.coredevice.ad9910 import (
    _AD9910_REG_PROFILE0,
    RAM_DEST_ASF,
    RAM_MODE_CONT_RAMPUP,
    RAM_MODE_DIRECTSWITCH,
)
from artiq.coredevice.suservo import Y_FULL_SCALE_MU
from artiq.coredevice.urukul import DEFAULT_PROFILE
from artiq.experiment import kernel
from artiq.language.core import at_mu, delay, delay_mu, now_mu
from artiq.language.units import kHz, ms, us

from ndscan.define.parameters import FloatParam, FloatParamHandle

from .lab_hardware import CS_TWEEZER_FREQ, UsesLabRTIOHardware


# SU-Servo always controls its DDS through single-tone profile 7.  Profile 6 differs
# by only one profile pin, making it a convenient home for the looping waveform.
MODULATION_PROFILE = 6
RAM_POINTS = 100

# The ordinary Cs configuration uses 8 dB of RF attenuation.  At the present imaging
# setpoint that leaves the servo integrator too close to full-scale for a symmetric
# positive excursion.  Removing just 1 dB before atoms are loaded gives the AD9910
# useful digital headroom while the closed loop preserves the requested optical power.
# Keeping this local avoids changing the dynamics of unrelated Cs experiments.
# MODULATION_ATTEN_DB = 7.0

# After returning DDS ownership to SU-Servo, allow one millisecond for the closed loop
# to settle before the following stage changes its setpoint for the hot-atom spill.
SERVO_RELOCK_TIME = 1.0 * ms


class CsTweezerRAMModulation(UsesLabRTIOHardware):
    """Modulate around the Cs servo's current, freshly held DDS amplitude.

    ``depth`` is fractional *DDS amplitude* modulation.  It is not necessarily the
    same fractional modulation of optical power through the AOM and RF chain.
    """

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        # This part uses timeline operations directly, so prepared-runtime kernel
        # validation requires it to declare its own core device attribute.
        self.setattr_device("core")

        self.frequency = self.setattr_param(
            "frequency",
            FloatParam,
            "Requested sine-wave modulation frequency",
            250.0 * kHz,
            min=1.0 * kHz,
            max=1_000.0 * kHz,
            unit="kHz",
            scale=kHz,
            step=1.0 * kHz,
        )
        self.frequency: FloatParamHandle

        self.depth = self.setattr_param(
            "depth",
            FloatParam,
            "Fractional DDS-amplitude excursion about the held servo value",
            # Start with a flat RAM waveform so the first run tests only the two
            # handovers.  The usable non-zero depth depends on how much DDS headroom
            # remains at the selected optical-power setpoint.
            0.0,
            min=0.0,
            max=0.9,
            step=0.01,
        )
        self.depth: FloatParamHandle

        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "Requested modulation time (rounded to a whole number of cycles)",
            100.0 * ms,
            min=1.0 * ms,
            max=5_000.0 * ms,
            unit="ms",
            scale=ms,
            step=10.0 * ms,
        )
        self.duration: FloatParamHandle

    def host_setup(self):
        super().host_setup()

        self.channel = self.hardware.suservo0_ch1
        self.suservo = self.hardware.suservo0
        self.suservo_profile = self.channel.servo_channel
        self.dds_channel = self.suservo_profile % 4
        self.shared_dds = self.channel.dds
        self.dds = self.shared_dds._inner_dds
        self.cpld = self.shared_dds.cpld

        # A profile register is one 64-bit word which the AD9910 interprets either as
        # single-tone settings or as RAM addresses.  At the fixed 80 MHz carrier, the
        # FTW therefore determines the RAM start address.  An otherwise irrelevant
        # phase offset lets the same P7 word encode an end address large enough to
        # upload one hold word, the sine, and at most three alignment words.
        self.carrier_ftw = int(self.dds.frequency_to_ftw(CS_TWEEZER_FREQ))
        self.loader_start = (self.carrier_ftw >> 14) & 0x3FF

        minimum_load_words = 1 + RAM_POINTS
        ftw_end_address_bits = (self.carrier_ftw >> 30) & 0x3
        minimum_loader_end = self.loader_start + minimum_load_words - 1
        self.ram_padding_words = (
            ftw_end_address_bits - (minimum_loader_end & 0x3)
        ) & 0x3
        self.ram_load_words = minimum_load_words + self.ram_padding_words
        self.loader_end = self.loader_start + self.ram_load_words - 1
        if self.loader_end > 0x3FF:
            raise ValueError("The P7 single-tone word cannot span this RAM upload")

        self.loader_pow = (self.loader_end >> 2) & 0xFF
        self.loader_phase = self.loader_pow / float(1 << 16)
        reconstructed_loader_end = (
            (self.loader_pow << 2) | ftw_end_address_bits
        )
        if reconstructed_loader_end != self.loader_end:
            raise ValueError("Could not encode the RAM loader end in P7's phase")

        self.hold_address = self.loader_start
        self.modulation_start = self.hold_address + 1
        self.modulation_end = self.modulation_start + RAM_POINTS - 1

        # The final 2*pi sample is deliberately absent.  Including both 0 and 2*pi
        # would duplicate one sample and make the wrap interval different from all
        # the others.
        self.sine_samples = [
            sin(2.0 * pi * i / RAM_POINTS) for i in range(RAM_POINTS)
        ]
        self.ram_amplitudes = [0.0] * self.ram_load_words
        self.ram_words = [0] * self.ram_load_words
        # This is deliberately not a kernel invariant: run() changes it while the Cs
        # DDS is detached from QSPI, and device_cleanup() reads it in the same resident
        # kernel if an exception interrupts that interval.
        self._owns_dds_slow_spi = False

        self.kernel_invariants = getattr(self, "kernel_invariants", set()) | {
            "channel",
            "suservo",
            "suservo_profile",
            "dds_channel",
            "shared_dds",
            "dds",
            "cpld",
            "carrier_ftw",
            "loader_start",
            "loader_end",
            "loader_pow",
            "loader_phase",
            "hold_address",
            "modulation_start",
            "modulation_end",
            "ram_padding_words",
            "ram_load_words",
            "sine_samples",
        }

    @kernel
    def device_setup(self):
        """Make P7 RAM-loader-compatible while the between-shot state is safe.

        Later stage changes update only the servo offset, so this harmless phase
        value survives until ``run()`` needs to reinterpret P7 as a RAM profile.  The
        output is also given a little more RF headroom before the servo is enabled;
        feedback subsequently establishes the same experimental optical setpoint.
        """
        self._owns_dds_slow_spi = False
        self.suservo.set_config(enable=0)
        # self.cpld.set_att(self.dds_channel, MODULATION_ATTEN_DB)
        self.channel.set_dds(
            profile=self.suservo_profile,
            frequency=CS_TWEEZER_FREQ,
            offset=0.0,
            phase=self.loader_phase,
        )
        self.suservo.set_config(enable=1)
        delay(5.0 * us)

    @kernel
    def run(self):
        """Play a sine about the current servo output, then restore the servo.

        Requires:
            The Cs servo is enabled and settled, and the resonant Cs light is off.
            :meth:`device_setup` has prepared profile 7 for the RAM handover.

        During:
            Holds the current servo output, temporarily hands the Cs DDS from QSPI to
            slow SPI, uploads and plays the amplitude-RAM waveform for a whole number
            of cycles close to ``duration``, then returns through the held amplitude.

        Leaves:
            The DDS back in single-tone/QSPI mode on profile 7, the RF output on, and
            the Cs servo closed at the same experimental setpoint after its relock
            delay. Resonant-light state is unchanged. No earlier hardware state beyond
            this explicit contract is restored automatically.
        """
        frequency = self.frequency.use()
        depth = self.depth.use()
        requested_duration = self.duration.use()

        # RAM advances on AD9910 SYNC_CLK (SYSCLK/4).  Its integer step therefore
        # quantises the requested frequency slightly.
        ram_step = int(
            self.dds.sysclk / (4.0 * RAM_POINTS * frequency) + 0.5
        )
        if ram_step < 1 or ram_step > 0xFFFF:
            raise ValueError("Requested Cs modulation frequency is not representable")

        actual_frequency = self.dds.sysclk / (
            4.0 * RAM_POINTS * ram_step
        )
        modulation_cycles = int(requested_duration * actual_frequency + 0.5)
        if modulation_cycles < 1:
            modulation_cycles = 1

        # Calculate timing from integer DDS clocks.  This guarantees that playback
        # ends on the same centre crossing at which it began.
        ram_period_mu_numerator = 4 * RAM_POINTS * ram_step
        if ram_period_mu_numerator % self.dds.sysclk_per_mu != 0:
            raise ValueError("One Cs RAM period is not an integer number of RTIO mu")
        ram_period_mu = ram_period_mu_numerator // self.dds.sysclk_per_mu
        actual_duration_mu = int64(modulation_cycles) * ram_period_mu

        # Freeze feedback first so its last output reaches the DDS, then stop the
        # global pipeline before reading the integrator state.  The RF output remains
        # enabled and P7 keeps producing that final amplitude.
        self.channel.set(
            en_out=1,
            en_iir=0,
            profile=self.suservo_profile,
        )
        delay(5.0 * us)
        self.suservo.set_config(enable=0)
        delay(5.0 * us)
        y0_mu = self.channel.get_y_mu(self.suservo_profile)
        self.core.break_realtime()

        centre = y0_mu / float(Y_FULL_SCALE_MU)
        if centre <= 0.0:
            raise ValueError("The held Cs servo amplitude is zero")
        maximum_symmetric_depth = (1.0 - centre) / centre
        if depth > maximum_symmetric_depth:
            # Core-device exceptions cannot conveniently interpolate values into
            # their message, so print the useful diagnostics immediately before the
            # concise exception.  This only runs on the error path and will not spam
            # an ordinary repeated experiment.
            print("Held Cs DDS amplitude:", centre)
            print("Requested fractional modulation depth:", depth)
            print("Largest symmetric depth at this setpoint:", maximum_symmetric_depth)
            raise ValueError("Cs modulation would exceed full DDS amplitude")

        # RAM writes appear in reverse address order.  Start every word at the centre
        # value (covering P7's hold word and the unused padding), then insert the sine
        # in the reversed locations belonging to P6.
        for i in range(self.ram_load_words):
            self.ram_amplitudes[i] = centre
        for i in range(RAM_POINTS):
            amplitude = centre * (1.0 + depth * self.sine_samples[i])
            self.ram_amplitudes[self.ram_load_words - 2 - i] = amplitude
        self.dds.amplitude_to_ram(self.ram_amplitudes, self.ram_words)

        # MASK_NU hands only the Cs DDS from SU-Servo's QSPI engine to slow SPI.
        # P7 stays selected as an ordinary single tone while RAM is uploaded, so the
        # light remains at y0 throughout the comparatively slow serial transfer.
        self.channel.set(
            en_out=1,
            en_iir=0,
            profile=self.suservo_profile,
        )
        # Set the flag first: if anything fails from this point until QSPI is restored,
        # device_cleanup() must undo the per-channel slow-SPI handover.
        self._owns_dds_slow_spi = True
        self.shared_dds.update_dds_sel(self.dds_channel)
        self.core.break_realtime()
        self.cpld.set_profile(self.dds_channel, DEFAULT_PROFILE)
        self.dds.set_cfr1(ram_enable=0)
        self.dds.io_update.pulse_mu(8)
        self.dds.write_ram(self.ram_words)

        # In amplitude-RAM mode the carrier frequency and phase come from the
        # standalone FTW/POW registers.  Match the ordinary P7 values.
        self.dds.set_ftw(self.carrier_ftw)
        self.dds.set_pow(self.loader_pow)
        self.dds.set_profile_ram(
            start=self.hold_address,
            end=self.hold_address,
            step=1,
            profile=DEFAULT_PROFILE,
            mode=RAM_MODE_DIRECTSWITCH,
        )
        self.dds.set_profile_ram(
            start=self.modulation_start,
            end=self.modulation_end,
            step=ram_step,
            profile=MODULATION_PROFILE,
            mode=RAM_MODE_CONT_RAMPUP,
        )
        self.dds.set_cfr1(ram_enable=1, ram_destination=RAM_DEST_ASF)

        # Reinterpret selected P7 as its one-word RAM hold.  Its value is y0, so this
        # first mode change is amplitude-continuous.
        self.cpld.cfg_io_update_all(1 << self.dds_channel)
        delay_mu(8)
        self.cpld.cfg_io_update_all(0)

        # Measure the duration of a profile write so the later return write can start
        # early enough for the physical P6 -> P7 edge to land at a centre crossing.
        cfg_write_start_mu = now_mu()
        self.cpld.set_profile(self.dds_channel, DEFAULT_PROFILE)
        cfg_write_duration_mu = now_mu() - cfg_write_start_mu

        self.cpld.set_profile(self.dds_channel, MODULATION_PROFILE)
        ram_start_mu = now_mu()
        return_profile_write_at_mu = (
            ram_start_mu + actual_duration_mu - cfg_write_duration_mu
        )
        if now_mu() >= return_profile_write_at_mu:
            raise ValueError("Cs modulation duration is too short for profile return")
        at_mu(return_profile_write_at_mu)
        self.cpld.set_profile(self.dds_channel, DEFAULT_PROFILE)

        # P7 now holds y0 in RAM, giving us time to restore its original single-tone
        # word.  Applying that word and RAM-disable together makes the second mode
        # change amplitude-continuous too.
        asf_mu = self.dds.amplitude_to_asf(centre)
        self.dds.write64(
            _AD9910_REG_PROFILE0 + DEFAULT_PROFILE,
            (asf_mu << 16) | (self.loader_pow & 0xFFFF),
            self.carrier_ftw,
        )
        self.dds.set_cfr1(ram_enable=0)
        self.cpld.cfg_io_update_all(1 << self.dds_channel)
        delay_mu(8)
        self.cpld.cfg_io_update_all(0)
        self.cpld.cfg_mask_nu_all(0)
        self._owns_dds_slow_spi = False

        # Give the first QSPI cycle the explicitly restored y0 before feedback is
        # allowed to update the integrator again.
        self.channel.set_y_mu(profile=self.suservo_profile, y=y0_mu)
        self.channel.set(
            en_out=1,
            en_iir=0,
            profile=self.suservo_profile,
        )
        self.suservo.set_config(enable=1)
        delay(5.0 * us)
        self.channel.set(
            en_out=1,
            en_iir=1,
            profile=self.suservo_profile,
        )
        delay(SERVO_RELOCK_TIME)

    @kernel
    def device_cleanup(self):
        """Recover only if this part was interrupted while it owned slow SPI.

        The ordinary laboratory safe state intentionally knows nothing about AD9910
        RAM.  Keeping this exceptional recovery beside the matching handover avoids
        touching the hidden DDS registers several times during every normal shot.
        """
        if not self._owns_dds_slow_spi:
            return

        self.core.break_realtime()
        self.suservo.set_config(enable=0)
        delay(5.0 * us)

        # The RF switch is an RTIO control and turns off independently of QSPI, so do
        # this before repairing the DDS register mode.
        self.channel.set(
            en_out=0,
            en_iir=0,
            profile=self.suservo_profile,
        )

        # This is the same exceptional cleanup used by the working oscilloscope test:
        # select this DDS, disable RAM, select P7, and hand it back to QSPI.
        self.shared_dds.update_dds_sel(self.dds_channel)
        self.core.break_realtime()
        self.dds.set_cfr1(ram_enable=0)
        self.dds.io_update.pulse_mu(8)
        self.cpld.set_profile(self.dds_channel, DEFAULT_PROFILE)
        self.cpld.cfg_mask_nu_all(0)
        self.channel.set_y(profile=self.suservo_profile, y=0.0)
        self._owns_dds_slow_spi = False
