"""The shared hardware and lifecycle used by laboratory experiments.

The important ownership boundary here is the physical board, not an experimental
idea such as "the Rb MOT".  Zotino and SU-Servo initialisation are board-wide
operations, so the whole apparatus is initialised and made safe coherently even when a
shot only uses one species.

Experimental parts receive the one :class:`LabRTIOHardware` instance as a non-owning
reference.  Explicit method names such as ``program_rb_light()`` make it clear which
part of the apparatus is being changed without introducing forwarding interfaces.

The lifecycle is here too because it is the one standard policy for every experiment:
initialise this hardware once after starting or resuming, establish the safe state
before each shot, and return to it when the experiment exits.
"""

from numpy import int64

from artiq.coredevice.ad9910 import AD9910
from artiq.coredevice.core import Core
from artiq.coredevice.suservo import Channel as SUServoChannel
from artiq.coredevice.suservo import SUServo
from artiq.coredevice.ttl import TTLOut
from artiq.coredevice.urukul import CPLD
from artiq.coredevice.zotino import Zotino
from artiq.experiment import kernel
from artiq.language.core import delay, delay_mu
from artiq.language.units import MHz, V, ms, us

from ndscan.define.fragment import Fragment


# These are electrical/configuration constants rather than experimental parameters.
# Stage frequencies, amplitudes, fields, and durations remain in the species parts.
DDS_ATTEN_DB = 8.0
SUSERVO_DAC_FULL_SCALE_V = 10.24

RB_TWEEZER_FREQ = 80.0 * MHz
RB_TWEEZER_ATTEN_DB = 8.0
RB_TWEEZER_ADC_CHANNEL = 0
RB_TWEEZER_PGIA_GAIN = 0
RB_TWEEZER_KP = -1.8
RB_TWEEZER_KI = -1_550_000.0
RB_TWEEZER_GAIN_LIMIT = 0.0

CS_TWEEZER_FREQ = 80.0 * MHz
CS_TWEEZER_ATTEN_DB = 8.0
CS_TWEEZER_ADC_CHANNEL = 1
CS_TWEEZER_PGIA_GAIN = 0
CS_TWEEZER_KP = -0.25
CS_TWEEZER_KI = -15_000.0
CS_TWEEZER_GAIN_LIMIT = 0.0


class LabRTIOHardware(Fragment):
    """Own, initialise, safe, and operate the currently used laboratory hardware.

    The name is an intentional boundary: host-side devices, RPC controllers, datasets,
    and analysis do not belong here. Add newly used RTIO hardware as the apparatus
    grows. This class contains device configuration and explicit-value actions, but no
    ndscan parameters.
    """

    def build_fragment(self):
        self.setattr_device("core")
        self.core: Core

        # Species-specific cooling/repump DDS hardware.
        self.setattr_device("dds_cpld_rb")
        self.dds_cpld_rb: CPLD
        self.setattr_device("dds_ch_rb_cool")
        self.dds_ch_rb_cool: AD9910
        self.setattr_device("dds_ch_rb_repump")
        self.dds_ch_rb_repump: AD9910

        self.setattr_device("dds_cpld_cs")
        self.dds_cpld_cs: CPLD
        self.setattr_device("dds_ch_cs_cool")
        self.dds_ch_cs_cool: AD9910
        self.setattr_device("dds_ch_cs_repump")
        self.dds_ch_cs_repump: AD9910

        # Shared analogue-field board.
        self.setattr_device("zotino0")
        self.zotino0: Zotino

        # One SU-Servo engine owns both presently used tweezer channels.
        self.setattr_device("suservo0")
        self.suservo0: SUServo
        self.setattr_device("suservo0_ch0")
        self.suservo0_ch0: SUServoChannel
        self.setattr_device("suservo0_ch1")
        self.suservo0_ch1: SUServoChannel

        # Shared triggers/enables and species-specific shutters.
        self.setattr_device("ttl_camera_exposure")
        self.ttl_camera_exposure: TTLOut
        self.setattr_device("ttl_quad")
        self.ttl_quad: TTLOut
        self.setattr_device("ttl_rb_cool_shut")
        self.ttl_rb_cool_shut: TTLOut
        self.setattr_device("ttl_rb_repump_shut")
        self.ttl_rb_repump_shut: TTLOut
        self.setattr_device("ttl_cs_cool_shut")
        self.ttl_cs_cool_shut: TTLOut
        self.setattr_device("ttl_cs_repump_shut")
        self.ttl_cs_repump_shut: TTLOut

    def host_setup(self):
        super().host_setup()

        # These profile/channel identities come from device_db.  Resolve them once on
        # the host and mark them invariant so kernels can use them as constants.
        self.rb_suservo_profile = self.suservo0_ch0.servo_channel
        self.rb_suservo_attenuator_channel = self.rb_suservo_profile % 4
        self.cs_suservo_profile = self.suservo0_ch1.servo_channel
        self.cs_suservo_attenuator_channel = self.cs_suservo_profile % 4
        self.suservo_cpld = self.suservo0_ch0.dds.cpld

        self.kernel_invariants = getattr(self, "kernel_invariants", set()) | {
            "rb_suservo_profile",
            "rb_suservo_attenuator_channel",
            "cs_suservo_profile",
            "cs_suservo_attenuator_channel",
            "suservo_cpld",
        }

    @kernel
    def initialise(self):
        """Initialise every currently owned board once, in a deterministic order."""
        self.core.reset()
        delay(10.0 * ms)

        # DDS initialisation can include PLL lock checks.  The explicit slack mirrors
        # the previous working single-species sequences and avoids sporadic underflow.
        self.dds_cpld_rb.init()
        self.core.break_realtime()
        delay(40.0 * ms)
        self.dds_ch_rb_cool.init()
        self.core.break_realtime()
        delay(40.0 * ms)
        self.dds_ch_rb_repump.init()
        self.core.break_realtime()
        delay(40.0 * ms)
        self.dds_cpld_cs.init()
        self.core.break_realtime()
        delay(40.0 * ms)
        self.dds_ch_cs_cool.init()
        self.core.break_realtime()
        delay(40.0 * ms)
        self.dds_ch_cs_repump.init()
        self.core.break_realtime()

        self.zotino0.init()

        # SU-Servo is one shared engine.  Initialise it once, stop its global write
        # cycle, configure both used channels, then restart the write cycle once.
        self.core.break_realtime()
        self.suservo0.init()
        self.core.break_realtime()
        delay(1.0 * ms)

        self.suservo0.set_config(enable=0)
        self.suservo0.set_pgia_mu(RB_TWEEZER_ADC_CHANNEL, RB_TWEEZER_PGIA_GAIN)
        self.suservo0.set_pgia_mu(CS_TWEEZER_ADC_CHANNEL, CS_TWEEZER_PGIA_GAIN)

        self.suservo_cpld.set_att(
            self.rb_suservo_attenuator_channel,
            RB_TWEEZER_ATTEN_DB,
        )
        self.suservo_cpld.set_att(
            self.cs_suservo_attenuator_channel,
            CS_TWEEZER_ATTEN_DB,
        )

        self.suservo0_ch0.set_iir(
            profile=self.rb_suservo_profile,
            adc=RB_TWEEZER_ADC_CHANNEL,
            kp=RB_TWEEZER_KP,
            ki=RB_TWEEZER_KI,
            g=RB_TWEEZER_GAIN_LIMIT,
        )
        self.suservo0_ch0.set_dds(
            profile=self.rb_suservo_profile,
            frequency=RB_TWEEZER_FREQ,
            # Experimental setpoints belong to the stages. Initialise with no demand
            # and let RbMOTStage program its selected value before enabling output.
            offset=0.0,
        )
        self.suservo0_ch0.set_y(profile=self.rb_suservo_profile, y=0.0)
        self.suservo0_ch0.set(
            en_out=0,
            en_iir=0,
            profile=self.rb_suservo_profile,
        )

        self.suservo0_ch1.set_iir(
            profile=self.cs_suservo_profile,
            adc=CS_TWEEZER_ADC_CHANNEL,
            kp=CS_TWEEZER_KP,
            ki=CS_TWEEZER_KI,
            g=CS_TWEEZER_GAIN_LIMIT,
        )
        self.suservo0_ch1.set_dds(
            profile=self.cs_suservo_profile,
            frequency=CS_TWEEZER_FREQ,
            offset=0.0,
        )
        self.suservo0_ch1.set_y(profile=self.cs_suservo_profile, y=0.0)
        self.suservo0_ch1.set(
            en_out=0,
            en_iir=0,
            profile=self.cs_suservo_profile,
        )

        self.suservo0.set_config(enable=1)
        self.core.break_realtime()

    @kernel
    def set_safe(self):
        """Put all currently owned outputs into the common laboratory safe state."""
        self.core.break_realtime()

        self.ttl_camera_exposure.off()
        self.ttl_quad.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()
        self.ttl_cs_cool_shut.off()
        self.ttl_cs_repump_shut.off()

        # These four shutters and the four Urukul switches below all live on DRTIO
        # destination 2.  Eight zero-duration events at one timestamp sit on (or can
        # exceed) the satellite SED lane limit.  Advancing by one coarse RTIO cycle
        # makes the event ordering strictly increasing without adding a physically
        # meaningful delay to the safe transition.
        delay_mu(int64(self.core.ref_multiplier))

        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.dds_ch_cs_cool.sw.off()
        self.dds_ch_cs_repump.sw.off()

        # Channels 0--3 are the currently owned field outputs.  Add further channels
        # here, with their documented safe values, as analogue hardware is introduced.
        self.zotino0.set_dac(
            [0.0 * V, 0.0 * V, 0.0 * V, 0.0 * V],
            [0, 1, 2, 3],
        )

        delay(1.0 * ms)
        self.dds_ch_rb_cool.set_att(DDS_ATTEN_DB)
        self.dds_ch_rb_repump.set_att(DDS_ATTEN_DB)
        self.dds_ch_cs_cool.set_att(DDS_ATTEN_DB)
        self.dds_ch_cs_repump.set_att(DDS_ATTEN_DB)

        # Stop the shared write cycle only once while making both channels safe.
        self.suservo0.set_config(enable=0)
        self.suservo0_ch0.set_y(profile=self.rb_suservo_profile, y=0.0)
        self.suservo0_ch0.set(
            en_out=0,
            en_iir=0,
            profile=self.rb_suservo_profile,
        )
        self.suservo0_ch1.set_y(profile=self.cs_suservo_profile, y=0.0)
        self.suservo0_ch1.set(
            en_out=0,
            en_iir=0,
            profile=self.cs_suservo_profile,
        )
        self.suservo0.set_config(enable=1)

    # -- Shared fields and camera trigger -----------------------------------------

    @kernel
    def set_fields_with_quad_demand(self, ew, ud, ns, quad):
        self.zotino0.set_dac([ew, ud, ns, quad], [0, 1, 2, 3])

    @kernel
    def set_fields_with_quad_demand_off(self, ew, ud, ns):
        self.zotino0.set_dac([ew, ud, ns, 0.0 * V], [0, 1, 2, 3])

    @kernel
    def turn_quad_on(self):
        self.ttl_quad.on()

    @kernel
    def turn_quad_off(self):
        self.ttl_quad.off()

    @kernel
    def start_camera_exposure(self):
        self.ttl_camera_exposure.on()

    @kernel
    def stop_camera_exposure(self):
        self.ttl_camera_exposure.off()

    # -- Rb-specific actions -------------------------------------------------------

    @kernel
    def program_rb_light(self, cool_frequency, repump_frequency, cool_amp, repump_amp):
        self.dds_ch_rb_cool.set(cool_frequency, amplitude=cool_amp)
        self.dds_ch_rb_repump.set(repump_frequency, amplitude=repump_amp)

    @kernel
    def turn_rb_light_on(self, shutter_prefire):
        self.ttl_rb_cool_shut.on()
        self.ttl_rb_repump_shut.on()
        delay(shutter_prefire)
        self.dds_ch_rb_cool.sw.on()
        self.dds_ch_rb_repump.sw.on()

    @kernel
    def turn_rb_light_off(self):
        self.dds_ch_rb_cool.sw.off()
        self.dds_ch_rb_repump.sw.off()
        self.ttl_rb_cool_shut.off()
        self.ttl_rb_repump_shut.off()

    @kernel
    def set_rb_tweezer_servo_enabled(self, enabled):
        if enabled:
            # The Rb loop is restarted from a known integrator value each time.
            self.suservo0_ch0.set(
                en_out=0,
                en_iir=0,
                profile=self.rb_suservo_profile,
            )
            self.suservo0_ch0.set_y(profile=self.rb_suservo_profile, y=0.0)
            self.suservo0_ch0.set(
                en_out=1,
                en_iir=1,
                profile=self.rb_suservo_profile,
            )
        else:
            self.suservo0_ch0.set(
                en_out=0,
                en_iir=0,
                profile=self.rb_suservo_profile,
            )

    @kernel
    def set_rb_tweezer_setpoint(self, setpoint_v):
        offset = (
            -setpoint_v
            * (10.0**RB_TWEEZER_PGIA_GAIN)
            / SUSERVO_DAC_FULL_SCALE_V
        )
        self.suservo0_ch0.set_dds_offset(
            profile=self.rb_suservo_profile,
            offset=offset,
        )

    # -- Cs-specific actions -------------------------------------------------------

    @kernel
    def program_cs_light(self, cool_frequency, repump_frequency, cool_amp, repump_amp):
        self.dds_ch_cs_cool.set(cool_frequency, amplitude=cool_amp)
        self.dds_ch_cs_repump.set(repump_frequency, amplitude=repump_amp)

    @kernel
    def turn_cs_light_on(self, shutter_prefire):
        """Shutters turn on signal sent *now*, cool+repump come on after shutter_prefire.
         Advances the timeline by shutter_prefire."""
        self.ttl_cs_cool_shut.on()
        self.ttl_cs_repump_shut.on()
        delay(shutter_prefire)
        self.dds_ch_cs_cool.sw.on()
        self.dds_ch_cs_repump.sw.on()

    @kernel
    def turn_cs_light_off(self):
        self.dds_ch_cs_cool.sw.off()
        self.dds_ch_cs_repump.sw.off()
        self.ttl_cs_cool_shut.off()
        self.ttl_cs_repump_shut.off()

    @kernel
    def set_cs_tweezer_servo_enabled(self, enabled):
        self.suservo0_ch1.set(
            en_out=enabled,
            en_iir=enabled,
            profile=self.cs_suservo_profile,
        )

    @kernel
    def set_cs_tweezer_setpoint(self, setpoint_v):
        offset = (
            -setpoint_v
            * (10.0**CS_TWEEZER_PGIA_GAIN)
            / SUSERVO_DAC_FULL_SCALE_V
        )
        self.suservo0_ch1.set_dds_offset(
            profile=self.cs_suservo_profile,
            offset=offset,
        )

    @kernel
    def drop_cs_trap(self, time_s):
        """Disable Cs trap for time_s, IIR updates are disabled while dropped to stop integrator
        wind up. There is 10+10=20 us of time to padded either side to stop windup."""
        self.suservo0_ch1.set(  # stop integrator
            en_out=1,
            en_iir=0,
            profile=self.cs_suservo_profile,
        )
        delay(10*us)
        self.suservo0_ch1.set( # RF Switch off
            en_out=0,
            en_iir=0,
            profile=self.cs_suservo_profile,
        )
        delay(time_s)
        self.suservo0_ch1.set( # RF Switch on
            en_out=1,
            en_iir=0,
            profile=self.cs_suservo_profile,
        )
        delay(10*us)
        self.suservo0_ch1.set( # Re-enable servo
            en_out=1,
            en_iir=0,
            profile=self.cs_suservo_profile,
        )



class UsesLabRTIOHardware(Fragment):
    """Base for a part which borrows the shared :class:`LabRTIOHardware` instance.

    This only stores the reference and makes it safe to follow from kernel code.  It
    does not own, initialise, wrap, or rename the hardware; subclasses continue to call
    explicit methods such as ``program_rb_light()`` directly.
    """

    def _use_hardware(self, hardware):
        self.hardware: LabRTIOHardware = hardware
        self.kernel_invariants = getattr(self, "kernel_invariants", set()) | {
            "hardware"
        }


class LabLifecycle(Fragment):
    """Apply the standard initial-safe/final-safe laboratory lifecycle.

    ``hardware`` is the one owned :class:`LabRTIOHardware` instance. Keeping the
    policy beside the safe-state implementation gives an experiment author one place
    to look for what happens at startup, between shots, after a scheduler pause, and
    at exit.

    For now the safe state is also the repeatable between-shot state.  If that ever
    becomes too slow, ``before_shot()`` is the single place where a faster repeatable
    boundary can be introduced without weakening final cleanup.
    """

    def build_fragment(self, hardware):
        self.hardware = hardware
        self._needs_initialisation = True
        self.kernel_invariants = getattr(self, "kernel_invariants", set()) | {
            "hardware"
        }

    def host_setup(self):
        super().host_setup()

        # host_setup() is entered again after a scheduler pause.  Another experiment
        # may have changed the apparatus while this experiment yielded, so initialise
        # once more before its next shot.
        self._needs_initialisation = True

    @kernel
    def before_shot(self):
        """Initialise once when needed, then establish the repeatable safe state."""
        if self._needs_initialisation:
            self._needs_initialisation = False
            self.hardware.initialise()
        self.hardware.set_safe()

    @kernel
    def device_setup(self):
        # ndscan invokes device_setup() before every run_once().
        self.before_shot()

    @kernel
    def device_cleanup(self):
        # Final cleanup always uses the genuinely safe state, even if before_shot()
        # later becomes a faster, less conservative between-shot transition.
        self.hardware.set_safe()


class LabEnvironment(Fragment):
    """Own the shared laboratory hardware and its one standard lifecycle."""

    def build_fragment(self):
        self.setattr_fragment("hardware", LabRTIOHardware)
        self.hardware: LabRTIOHardware

        self.setattr_fragment(
            "lifecycle",
            LabLifecycle,
            hardware=self.hardware,
        )
        self.lifecycle: LabLifecycle
