from artiq.language.core import kernel
from artiq.language.environment import BooleanValue
from artiq.language.environment import EnvExperiment
from artiq.language.environment import NumberValue
from artiq.language.units import MHz


# Nuances:
# - This uses SU-Servo as a constant-amplitude DDS driver, not as a feedback loop.
# - The fixed output amplitude is the SU-Servo `y` value / DDS ASF; IIR feedback is
#   kept disabled with `en_iir=0`, so the Sampler input does not affect the output.
# - The selected channel 0-7 uses the same-numbered SU-Servo profile. Attenuation is
#   written to the Urukul channel belonging to the selected SU-Servo output.
# - The global SU-Servo engine is still enabled so the profile/ASF are applied to
#   the DDS in SU-Servo mode.
# - Running this calls `suservo0.init()` and toggles the global SU-Servo engine.
#   That disturbs all channels on this SUServo if any of them are actively servoing.
class SUServoConstantRF(EnvExperiment):
    def build(self):
        self.setattr_device("core")
        self.setattr_device("suservo0")
        self.setattr_argument(
            "frequency",
            NumberValue(
                80 * MHz,
                unit="MHz",
                min=0.0,
            ),
        )
        self.setattr_argument(
            "attenuation",
            NumberValue(
                8.0,
                unit="dB",
                min=0.0,
                max=31.5,
            ),
        )
        self.setattr_argument(
            "asf",
            NumberValue(0.5, step=0.001, min=0.0, max=0.999, precision=4),
        )
        self.setattr_argument(
            "channel",
            NumberValue(
                default=0,
                step=1,
                min=0,
                max=7,
                precision=0,
                type="int",
            ),
        )
        self.setattr_argument("rf_switch_on", BooleanValue(True))

    def prepare(self):
        self.channel_index = int(self.channel)
        self.suservo_channel = self.get_device(f"suservo0_ch{self.channel_index}")
        self.suservo_profile = self.suservo_channel.servo_channel
        self.attenuator_channel = self.suservo_channel.servo_channel % 4
        self.suservo_cpld = self.suservo_channel.dds.cpld

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {
            "suservo_channel",
            "suservo_profile",
            "attenuator_channel",
            "suservo_cpld",
        }

    @kernel
    def run(self):
        # Prepare core
        self.core.reset()
        self.core.break_realtime()

        # Initialize the whole SU-Servo parent device. This is global to all 8 channels.
        self.suservo0.init()
        self.suservo0.set_config(enable=0)   # Not necessary (done in init) but for completeness

        f = self.frequency                       # frequency (Hz) of Urukul output
        attenuation = self.attenuation  # dB
        asf = self.asf

        # Set attenuation on the selected Urukul channel. The channel index is modulo 4
        # because each Urukul CPLD has four attenuators, while SUServo has 8 channels.
        self.suservo_cpld.set_att(
            self.attenuator_channel, attenuation
        )

        # Set integrator value (with en_iir=0, y is used as the fixed output ASF).
        self.suservo_channel.set_y(profile=self.suservo_profile, y=asf)

        self.suservo_channel.set_dds(
            profile=self.suservo_profile, frequency=f, offset=0.0
        ) # Set selected profile DDS coefficients
        self.suservo_channel.set(
            en_out=1 if self.rf_switch_on else 0,
            en_iir=0,
            profile=self.suservo_profile,
        ) # Set selected profile, with RF switch (on/off) and IIR updates (on/off)
        # Start global SUServo operation so the profile/ASF are applied to the DDS.
        self.suservo0.set_config(enable=1)
