"""Check AD9910 amplitude RAM operation on a DDS used by SU-Servo.

This deliberately tests only the 1066 nm tweezer channel (``suservo0_ch1``).
The sequence is:

1. Close the ordinary intensity servo and let it settle.
2. Freeze and read its final integrator value, ``y0``.
3. Stop the shared SU-Servo engine and use ``MASK_NU`` to hand DDS channel 1
   over to the ordinary SPI interface.
4. Use the still-active single-tone profile 7 as the loader for one RAM block.
5. Reinterpret profile 7 as a direct-switch RAM hold point at ``y0``, then use
   profile 6 for the sampled sine-wave modulation.
6. Return to profile 7's hold point, reinterpret it as the original single
   tone again, restart SU-Servo, and briefly check that the loop settles.

The RF switch remains enabled throughout both live handovers.  It is only
disabled by the final safe-state cleanup after the test has finished.  Observe
the RF envelope, or the photodiode signal, on an oscilloscope.

Important: ``modulation_depth`` is a fractional modulation of DDS *amplitude*.
It is not necessarily the same fractional modulation of optical power.
"""

from math import pi, sin

from artiq.coredevice.ad9910 import (
    _AD9910_REG_PROFILE0,
    RAM_DEST_ASF,
    RAM_MODE_CONT_RAMPUP,
    RAM_MODE_DIRECTSWITCH,
)
from artiq.coredevice.suservo import Y_FULL_SCALE_MU
from artiq.coredevice.urukul import DEFAULT_PROFILE
from artiq.experiment import EnvExperiment, NumberValue, kernel
from artiq.language.core import at_mu, delay, delay_mu, now_mu
from artiq.language.types import TInt32
from artiq.language.units import MHz, kHz, ms, us


# Electrical settings copied from the working Cs/1066 nm sequence.
SUSERVO_CHANNEL = 1
ADC_CHANNEL = 1
PGIA_GAIN = 0
ATTENUATION_DB = 8.0
CARRIER_FREQUENCY = 80.0 * MHz
KP = -0.25
KI = -15_000.0
GAIN_LIMIT = 0.0

# SU-Servo's QSPI engine always writes AD9910 single-tone profile 7.  During
# RAM operation, the same physical profile becomes a one-word hold point;
# profile 6 contains the looping waveform.  The transition 7 (111) <-> 6 (110)
# changes only one profile pin.
MODULATION_PROFILE = 6
RAM_POINTS = 100


class SUServoRAMModulation1066(EnvExperiment):
    """Settle the 1066 nm servo, then modulate its held value using DDS RAM."""

    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_device("suservo0_ch0")
        self.setattr_device("suservo0_ch1")

        self.setattr_argument(
            "setpoint_v",
            NumberValue(
                5.6,
                unit="V",
                step=0.1,
                min=0.0,
                max=10.0,
                precision=3,
            ),
            tooltip="Photodiode-voltage setpoint used while acquiring y0.",
        )
        self.setattr_argument(
            "settling_time",
            NumberValue(
                100.0 * ms,
                unit="ms",
                scale=ms,
                step=10.0 * ms,
                min=1.0 * ms,
            ),
        )
        self.setattr_argument(
            "modulation_frequency",
            NumberValue(
                10.0 * kHz,
                unit="kHz",
                scale=kHz,
                step=1.0 * kHz,
                min=1.0 * kHz,
            ),
        )
        self.setattr_argument(
            "modulation_depth",
            NumberValue(
                0.10,
                step=0.01,
                min=0.0,
                max=0.9,
                precision=4,
            ),
            tooltip="Fractional high/low excursion of the DDS amplitude around y0.",
        )
        self.setattr_argument(
            "modulation_duration",
            NumberValue(
                100.0 * ms,
                unit="ms",
                scale=ms,
                step=10.0 * ms,
                min=1.0 * ms,
            ),
            tooltip="Rounded to a whole number of actual RAM waveform periods.",
        )
        self.setattr_argument(
            "restored_servo_time",
            NumberValue(
                20.0 * ms,
                unit="ms",
                scale=ms,
                step=5.0 * ms,
                min=1.0 * ms,
            ),
            tooltip="How long to run the restored closed loop before checking y again.",
        )

    def prepare(self):
        self.channel = self.suservo0_ch1
        self.other_used_channel = self.suservo0_ch0

        # A SU-Servo Channel profile (0..31) and an AD9910 hardware profile
        # (0..7) are different concepts.  Existing lab code conventionally uses
        # the channel number as its SU-Servo memory profile.
        self.suservo_profile = self.channel.servo_channel
        self.dds_channel = self.channel.servo_channel % 4

        # SharedDDS is the supported route for slow configuration SPI in
        # SU-Servo mode.  ARTIQ does not expose RAM helpers directly on
        # SharedDDS, so this small hardware test deliberately reaches its inner
        # AD9910.  Production code should hide this in one hardware-service API.
        self.shared_dds = self.channel.dds
        self.dds = self.shared_dds._inner_dds
        self.cpld = self.shared_dds.cpld

        # A profile register is not two separate pieces of storage: the AD9910
        # reinterprets the same 64 bits as either single-tone data or RAM control
        # data.  We exploit that dual interpretation for loading.  At 80 MHz,
        # the FTW bits happen to encode RAM start address 491.  We choose an
        # otherwise irrelevant RF phase so the POW bits encode a suitable end
        # address.  Profile 7 therefore remains a perfectly valid single tone
        # while also describing the boundaries of one large RAM upload.
        self.carrier_ftw = int(self.dds.frequency_to_ftw(CARRIER_FREQUENCY))
        self.loader_start = (self.carrier_ftw >> 14) & 0x3FF

        # The upload contains one direct-switch hold word, the complete sine,
        # and up to three unused padding words.  The two least-significant bits
        # of the loader end address overlap the FTW's two MSBs, so padding makes
        # the requested end address compatible without changing the carrier.
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

        # RAM end[9:2] overlaps single-tone POW[7:0].  Keep POW[15:8] zero;
        # those bits only affect the (irrelevant during loading) RAM step value.
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

        # AD9910 RAM advances on SYNC_CLK, which is SYSCLK/4.  The step value
        # is therefore the number of 4 ns intervals for which each sampled
        # sine value is held.  At 250 kHz, 100 points gives step=10:
        # 100 points * 10 * 4 ns = 4 us per period.
        self.ram_step = int(
            round(
                self.dds.sysclk
                / (4.0 * RAM_POINTS * self.modulation_frequency)
            )
        )
        if self.ram_step < 1 or self.ram_step > 0xFFFF:
            raise ValueError(
                "The requested modulation frequency cannot be represented by "
                f"a {RAM_POINTS}-point AD9910 RAM profile"
            )

        self.actual_modulation_frequency = self.dds.sysclk / (
            4.0 * RAM_POINTS * self.ram_step
        )

        # End on the same centre crossing at which playback starts.  This
        # makes the RAM-to-fixed transition amplitude-continuous instead of
        # stopping at an arbitrary point on the sine wave.
        self.modulation_cycles = max(
            1,
            int(round(self.modulation_duration * self.actual_modulation_frequency)),
        )
        # Derive the period from integer clock counts.  Converting 1/f through
        # floating-point seconds can truncate by one machine unit; repeated for
        # many cycles, that would move the return away from the centre crossing.
        ram_period_mu_numerator = 4 * RAM_POINTS * self.ram_step
        if ram_period_mu_numerator % self.dds.sysclk_per_mu != 0:
            raise ValueError("One RAM period is not an integer number of RTIO mu")
        self.ram_period_mu = (
            ram_period_mu_numerator // self.dds.sysclk_per_mu
        )
        self.actual_modulation_duration_mu = (
            self.modulation_cycles * self.ram_period_mu
        )
        self.actual_modulation_duration = self.core.mu_to_seconds(
            self.actual_modulation_duration_mu
        )

        self.ram_words = [0] * self.ram_load_words
        self.ram_readback = [0] * self.ram_load_words

        self.kernel_invariants = getattr(self, "kernel_invariants", set()) | {
            "channel",
            "other_used_channel",
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
            "ram_step",
            "ram_period_mu",
            "actual_modulation_duration_mu",
        }

    def run(self):
        try:
            y0_mu = int(self.acquire_held_servo_value())
            centre = y0_mu / Y_FULL_SCALE_MU

            # Do not include the sample at 2*pi: it is identical to the first
            # sample at zero and would create a duplicated point at the wrap.
            # With indices 0..N-1, the spacing from the final sample back to
            # the first is exactly the same as every other sample interval.
            amplitudes = [
                centre
                * (
                    1.0
                    + self.modulation_depth
                    * sin(2.0 * pi * i / RAM_POINTS)
                )
                for i in range(RAM_POINTS)
            ]
            low = min(amplitudes)
            high = max(amplitudes)

            if centre <= 0.0:
                raise ValueError(
                    "The settled servo amplitude was zero; there is no non-zero "
                    "carrier to modulate"
                )
            if low < 0.0 or high > 1.0:
                raise ValueError(
                    "The requested modulation would exceed the AD9910 amplitude "
                    f"range: low={low:.6f}, high={high:.6f}"
                )

            # The one upload block contains everything needed by both RAM
            # profiles: a fixed y0 word for P7, then P6's sine, then any padding
            # forced by P7's dual-use address encoding.  The padding is outside
            # both playback profiles and is never observed at the output.
            logical_ram = (
                [centre]
                + amplitudes
                + [centre] * self.ram_padding_words
            )

            # Let the ARTIQ driver encode the 14-bit ASF into each 32-bit RAM
            # word.  Besides documenting the intended RAM destination, this
            # also gives values the signed int32 representation expected by a
            # kernel argument.  AD9910 RAM serial writes appear in reverse
            # address order, so reverse the complete logical block.
            self.dds.amplitude_to_ram(
                list(reversed(logical_ram)),
                self.ram_words,
            )

            print("Held SU-Servo y0:", centre)
            print("Sine DDS amplitude range:", low, high)
            print(
                "Requested/actual modulation frequency [Hz]:",
                self.modulation_frequency,
                self.actual_modulation_frequency,
            )
            print("AD9910 RAM step:", self.ram_step)
            print(
                "P7 loader start/end, words, padding:",
                self.loader_start,
                self.loader_end,
                self.ram_load_words,
                self.ram_padding_words,
            )
            print("P7 loader phase [turns]:", self.loader_phase)
            print(
                "Modulation cycles/actual duration [s]:",
                self.modulation_cycles,
                self.actual_modulation_duration,
            )

            self.play_ram_and_check_restore(y0_mu)
        finally:
            # Also runs after a kernel exception, where possible.  The selected
            # output is left off, RAM is disabled, QSPI is restored, and the
            # shared engine is left running in the same style as the MOT safe state.
            self.make_safe()

    @kernel
    def acquire_held_servo_value(self) -> TInt32:
        """Close the Cs loop, then stop it and return its final integrator value."""
        self.core.reset()
        self.core.break_realtime()

        # init() is global: it resets/configures the Sampler and both SU-Servo
        # Urukuls.  This test must therefore not run alongside another user of
        # the same SU-Servo engine.
        self.suservo0.init()
        self.core.break_realtime()
        delay(1.0 * ms)

        self.suservo0.set_config(enable=0)

        # Explicitly close both currently used tweezer outputs before starting
        # the shared engine.  This prevents stale channel-control state from a
        # previous experiment from unexpectedly enabling the other species.
        self.other_used_channel.set(
            en_out=0,
            en_iir=0,
            profile=self.other_used_channel.servo_channel,
        )
        self.other_used_channel.set_y(
            profile=self.other_used_channel.servo_channel,
            y=0.0,
        )
        self.channel.set(
            en_out=0,
            en_iir=0,
            profile=self.suservo_profile,
        )

        self.suservo0.set_pgia_mu(ADC_CHANNEL, PGIA_GAIN)
        self.cpld.set_att(self.dds_channel, ATTENUATION_DB)
        self.channel.set_iir(
            profile=self.suservo_profile,
            adc=ADC_CHANNEL,
            kp=KP,
            ki=KI,
            g=GAIN_LIMIT,
        )
        self.channel.set_dds(
            profile=self.suservo_profile,
            frequency=CARRIER_FREQUENCY,
            offset=-self.setpoint_v * (10.0**PGIA_GAIN) / 10.24,
            # This small phase offset makes P7's overlapping bits describe the
            # desired RAM loader end address.  It has no effect on intensity.
            phase=self.loader_phase,
        )
        self.channel.set_y(profile=self.suservo_profile, y=0.0)
        self.channel.set(
            en_out=1,
            en_iir=1,
            profile=self.suservo_profile,
        )
        self.suservo0.set_config(enable=1)
        delay(self.settling_time)

        # Freeze the IIR first, allowing the final y to reach the DDS, then stop
        # the global ADC/IIR/QSPI pipeline.  The DDS keeps emitting its last
        # profile-7 value while the engine is stopped.
        self.channel.set(
            en_out=1,
            en_iir=0,
            profile=self.suservo_profile,
        )
        delay(5.0 * us)
        self.suservo0.set_config(enable=0)
        delay(5.0 * us)

        y0_mu = self.channel.get_y_mu(self.suservo_profile)
        self.core.break_realtime()
        adc_v = self.suservo0.get_adc(ADC_CHANNEL)
        print("Photodiode at handover [V]:", adc_v)
        return y0_mu

    @kernel
    def play_ram_and_check_restore(self, y0_mu):
        """Upload/play RAM without closing RF, then restore and briefly relock."""
        self.core.break_realtime()

        # acquire_held_servo_value() left profile 7 emitting the final servo
        # value.  Keep both the RF switch and the frozen IIR state unchanged
        # while ownership moves from QSPI to slow SPI.
        self.channel.set(
            en_out=1,
            en_iir=0,
            profile=self.suservo_profile,
        )

        # MASK_NU=1 disconnects this physical DDS from QSPI and connects CS=3
        # slow SPI plus CFG.IO_UPDATE.  The supplied Urukul gateware confirms
        # that this is the intended per-channel handover mechanism.
        self.shared_dds.update_dds_sel(self.dds_channel)
        self.core.break_realtime()

        # SU-Servo writes single-tone profile 7.  Keep that profile selected and
        # RAM disabled: its frequency, phase, and live y0 amplitude therefore
        # remain at the output throughout the following serial RAM transfer.
        self.cpld.set_profile(self.dds_channel, DEFAULT_PROFILE)
        self.dds.set_cfr1(ram_enable=0)
        self.dds.io_update.pulse_mu(8)

        # The active P7 single-tone word was deliberately constructed so that,
        # when the RAM loader reinterprets its overlapping bits, it describes
        # loader_start..loader_end.  One transfer consequently loads P7's hold
        # word, P6's whole sine, and any unused alignment padding.
        self.dds.write_ram(self.ram_words)

        # Readback is useful in this hardware test.  It can be removed from a
        # production sequence to save time; P7 remains an ordinary single tone
        # for the entire operation.
        self.core.break_realtime()
        self.dds.read_ram(self.ram_readback)
        for i in range(self.ram_load_words):
            if self.ram_readback[i] != self.ram_words[i]:
                raise ValueError("AD9910 RAM readback did not match the upload")
        print("RAM readback passed")
        self.core.break_realtime()

        # During amplitude RAM, frequency and phase come from the standalone
        # FTW and POW registers.  Use exactly the same values as single-tone P7
        # so the handover does not intentionally introduce a phase jump.
        self.dds.set_ftw(self.carrier_ftw)
        self.dds.set_pow(self.loader_pow)

        # In RAM mode P7 is a one-word, direct-switch hold point at y0.  P6 is
        # the looping sine.  Both profiles refer into the one block just loaded.
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
            step=self.ram_step,
            profile=MODULATION_PROFILE,
            mode=RAM_MODE_CONT_RAMPUP,
        )
        self.dds.set_cfr1(ram_enable=1, ram_destination=RAM_DEST_ASF)

        # A single I/O update reinterprets selected P7 as its direct-switch RAM
        # hold point.  That RAM word is y0, matching the single-tone amplitude
        # that was active immediately beforehand.
        self.cpld.cfg_io_update_all(1 << self.dds_channel)
        delay_mu(8)
        self.cpld.cfg_io_update_all(0)

        # CFR1[31] is RAM enable and CFR1[30:29] is the destination.  Reading
        # it back checks that amplitude-RAM mode itself was programmed, rather
        # than merely checking the RAM storage above.
        self.core.break_realtime()
        cfr1 = self.dds.read32(0)
        if ((cfr1 >> 29) & 0x7) != (0x4 | RAM_DEST_ASF):
            raise ValueError("AD9910 CFR1 readback says amplitude RAM is not enabled")
        print("Amplitude-RAM CFR1 readback passed")
        self.core.break_realtime()

        # Measure one CPLD configuration write.  Starting the later P6 -> P7
        # write this much early makes the physical profile-pin transition land
        # on an integer-period centre crossing.
        cfg_write_start_mu = now_mu()
        self.cpld.set_profile(self.dds_channel, DEFAULT_PROFILE)
        cfg_write_duration_mu = now_mu() - cfg_write_start_mu

        # P7 currently holds y0.  Selecting P6 starts its continuous sine at
        # the first sample, which is also y0.
        self.cpld.set_profile(self.dds_channel, MODULATION_PROFILE)
        ram_start_mu = now_mu()

        return_profile_write_at_mu = (
            ram_start_mu
            + self.actual_modulation_duration_mu
            - cfg_write_duration_mu
        )
        if now_mu() >= return_profile_write_at_mu:
            raise ValueError("Modulation duration is too short for profile return")
        at_mu(return_profile_write_at_mu)
        self.cpld.set_profile(self.dds_channel, DEFAULT_PROFILE)

        # P7 direct-switch RAM now holds y0 indefinitely, giving plenty of time
        # to buffer its original single-tone representation.  Applying that
        # profile word and RAM-disable bit together returns directly to the same
        # frequency, phase, and amplitude without another bridge profile.
        centre = y0_mu / Y_FULL_SCALE_MU
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

        # The SU-Servo memory state was never changed by RAM.  Writing y0 again
        # nevertheless makes that restoration explicit and gives the first QSPI
        # update the same amplitude that preceded the handover.
        self.channel.set_y_mu(profile=self.suservo_profile, y=y0_mu)
        self.channel.set(
            en_out=1,
            en_iir=0,
            profile=self.suservo_profile,
        )
        self.suservo0.set_config(enable=1)
        delay(5.0 * us)

        # The first QSPI update used the explicitly restored y0.  Only now
        # resume feedback, avoiding an integrator kick at the ownership change.
        self.channel.set(
            en_out=1,
            en_iir=1,
            profile=self.suservo_profile,
        )
        delay(self.restored_servo_time)

        # Freeze and stop once more so the readback cannot collide with an IIR
        # state write.  This value should normally be close to y0, apart from
        # ordinary loop settling/noise and plant drift.
        self.channel.set(
            en_out=1,
            en_iir=0,
            profile=self.suservo_profile,
        )
        delay(5.0 * us)
        self.suservo0.set_config(enable=0)
        delay(5.0 * us)
        restored_y_mu = self.channel.get_y_mu(self.suservo_profile)
        print(
            "Restored SU-Servo y:",
            restored_y_mu / Y_FULL_SCALE_MU,
        )

    @kernel
    def make_safe(self):
        """Leave the selected DDS out of RAM mode, back on QSPI, and RF-off."""
        self.core.break_realtime()
        self.suservo0.set_config(enable=0)
        delay(5.0 * us)

        self.channel.set(
            en_out=0,
            en_iir=0,
            profile=self.suservo_profile,
        )
        self.other_used_channel.set(
            en_out=0,
            en_iir=0,
            profile=self.other_used_channel.servo_channel,
        )
        self.other_used_channel.set_y(
            profile=self.other_used_channel.servo_channel,
            y=0.0,
        )

        # MASK_NU is required for the slow-SPI CFR1 write even during cleanup.
        self.shared_dds.update_dds_sel(self.dds_channel)
        self.core.break_realtime()
        self.dds.set_cfr1(ram_enable=0)
        self.dds.io_update.pulse_mu(8)
        self.cpld.set_profile(self.dds_channel, DEFAULT_PROFILE)
        self.cpld.cfg_mask_nu_all(0)

        self.channel.set_y(profile=self.suservo_profile, y=0.0)
        self.suservo0.set_config(enable=1)
        delay(5.0 * us)
