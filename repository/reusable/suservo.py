"""
This is lifted straight from 
https://gitlab.com/aion-physics/code/artiq/pyaion/-/blob/master/pyaion/fragments/suservo.py
"""

import logging
from typing import Set

from artiq.coredevice.core import Core
from artiq.coredevice.suservo import Channel
from artiq.coredevice.suservo import SUServo
from artiq.coredevice.urukul import CPLD
from artiq.experiment import delay_mu
from artiq.experiment import kernel
from artiq.experiment import rpc
from artiq.experiment import TBool
from artiq.experiment import TFloat
from artiq.experiment import TInt32
from ndscan.experiment import Fragment
from numpy import int64


logger = logging.getLogger(__name__)

DEFAULT_KI = -10000.0


class LibSetSUServoStatic(Fragment):
    """
    Set a static SUServo output

    The channel to be set should be passed as an argument to
    :meth:`.build_fragment`, e.g.::

        self.setattr_fragment(
            "LibSetSUServoStatic", LibSetSUServoStatic, "suservo0_ch0",
        )

    The :meth:`~ndscan.experiment.fragment.Fragment.device_setup` of this
    :class:`~ndscan.experiment.fragment.ExpFragment` will reinitialise the
    entire SUServo device. This Fragment then provides a :meth:`.set_suservo`
    method which can be used to set the specified channel's output.

    Unfortunately, it's not possible to read out the attenuation register from
    an Urukul in SUServo mode (see
    https://github.com/quartiq/urukul/blob/af67d56c0158d61d0232a491cf123a5e6767ecbf/urukul.py#L254-L282).
    This class must therefore reset the attenuations of all channels when it
    changes the attenuation of one - i.e. all SUServo outputs that share an
    Urukul with the one being written and that have not had their attenuation
    set during the current ARTIQ experiment will have their attenuations reset.
    """

    # A set of which suservos have been initiated, stored as a class variable
    # so that suservo devices are only initiated once. This is a set of ARTIQ
    # channels that represent suservos which have been initiated during this
    # EnvExperiment
    initiated_suservos: Set[int] = set()

    def build_fragment(self, channel: str):
        self.setattr_device("core")
        self.core: Core

        self.channel = channel

        # Kernel variables
        self.debug_enabled = logger.isEnabledFor(logging.DEBUG)
        self.first_run = True

        # %% Kernel invariants
        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {"debug_enabled"}

    def host_setup(self):
        super().host_setup()

        self.suservo_channel: Channel = self.get_device(self.channel)
        self.suservo: SUServo = self.suservo_channel.servo

        # These are conventions in the AION lab:
        self.sampler_channel: int = self.suservo_channel.servo_channel
        self.suservo_profile: int = self.suservo_channel.servo_channel

        self.kernel_invariants.add("suservo_channel")
        self.kernel_invariants.add("suservo")
        self.kernel_invariants.add("sampler_channel")
        self.kernel_invariants.add("suservo_profile")

    @rpc
    def mark_suservo_initiated(self, suservo_channel: TInt32) -> TBool:
        """
        Check whether the given suservo has been initiated and return True if it
        has. In either case, mark it as now having been initiated

        This is an RPC so that we can use python features like class members to
        communicate between instances. This could be done purely on the core
        using e.g. a singleton class, but that's more complex. Here I'll pay the
        few ms penalty for keeping the code simpler.
        """
        out = suservo_channel in self.__class__.initiated_suservos
        self.__class__.initiated_suservos.add(suservo_channel)
        return out

    @kernel
    def device_setup(self):
        self.device_setup_subfragments()

        # Initiate the suservo itself (i.e. all four channels)
        if self.first_run and not self.mark_suservo_initiated(self.suservo.channel):
            if self.debug_enabled:
                logger.info(
                    "Initiating suservo %s = artiq channel 0x%x",
                    self.channel,
                    self.suservo.channel,
                )

            self.core.break_realtime()
            self.suservo.init()
            self.suservo.set_config(enable=1)

        else:
            if self.debug_enabled:
                logger.info(
                    "Skipping suservo %s  - already initiated",
                    self.channel,
                )

        if self.first_run:
            self.first_run = False

            if self.debug_enabled:
                logger.info(
                    "Initiating suservo %s with default IIR and PGIA settings",
                    self.channel,
                )

            # Set default IIR settings
            self.core.break_realtime()
            self.set_iir_params()

            # Set the PGIA to 1x - there's no way to read it, so we have to have
            # a deterministic initialisation
            self.set_pgia_gain_mu(0)

    @kernel
    def setpoint_to_offset(self, setpoint_v: TFloat) -> TFloat:
        """Convert a setpoint in volts to a SUServo offset

        Args:
            setpoint_v (TFloat): Setpoint in volts

        Returns:
            TFloat: Offset in SUServo units
        """
        # Setpoints are stored in units of full scale, where 1.0 -> +10V, -1.0 -> -10V
        return -1.0 * setpoint_v / 10.0

    @kernel
    def set_this_attenuation(self, attenuation: TFloat):
        # Set the attenuator for this channel on this Urukul
        attenuator_channel = self.suservo_channel.servo_channel % 4
        cpld = self.suservo_channel.dds.cpld  # type: CPLD
        cpld.set_att(attenuator_channel, attenuation)

    @kernel
    def set_suservo(
        self,
        freq: TFloat,
        amplitude: TFloat,
        attenuation: TFloat = 30.0,
        rf_switch_state: TBool = True,
        setpoint_v: TFloat = 0.0,
        enable_iir: TBool = False,
    ):
        """Set a static output on a SUServo channel

        This method advances the timeline by at least 5 servo cycles (a bit more if the
        attenuation is changed).

        Args:
            freq (TFloat): Frequency in Hz
            amplitude (TFloat): Amplitude from 0 to 1 when 1 is 100% output.
            attenuation (TFloat, optional): Attenuation on the variable attenuator. Defaults to 30.0.
            rf_switch_state (TBool, optional): State of the RF switch. Defaults to on.
            setpoint_v (TFloat, optional): SUServo setpoint. Only relevant if enable_IRR=True. Defaults to 0.0.
            enable_iir (TBool, optional): Enable the servo loop. Defaults to False.
        """

        if self.debug_enabled:
            logger.info(
                "Setting channel %s to %f MHz, amp = %f, att = %f, rf_switch_state=%s, setpoint_v=%s, enable_iir=%s, suservo_profile=%s",
                self.channel,
                1e-6 * freq,
                amplitude,
                attenuation,
                rf_switch_state,
                setpoint_v,
                enable_iir,
                self.suservo_profile,
            )
            self.core.break_realtime()
            delay_mu(int64(self.core.ref_multiplier))

        # Set the attenuator for this channel
        self.set_this_attenuation(attenuation)

        # Configure this profile to have the requested amplitude and frequency
        self.suservo_channel.set_y(profile=self.suservo_profile, y=amplitude)
        self.suservo_channel.set_dds(
            profile=self.suservo_profile,
            offset=self.setpoint_to_offset(setpoint_v),
            frequency=freq,
            phase=0.0,
        )

        # Set channel output state
        self.set_channel_state(rf_switch_state, enable_iir)

    @kernel
    def set_pgia_gain_mu(self, gain_mu):
        """
        Set the PGIA gain of this channel

        See :meth:`artiq.coredevice.suservo.SUServo.set_pgia_mu` for details.
        """
        self.suservo.set_pgia_mu(self.sampler_channel, gain_mu)

    @kernel
    def set_setpoint(self, new_setpoint: TFloat):
        """Set the SUServo setpoint

        Updates only the SUServo setpoint. Does not enable the SUServo / RF switch or change any other parameters.

        Args:
            new_offset (TFloat): The new offset in volts_
        """
        if self.debug_enabled:
            logger.info(
                "Setting setpoint for %s: %s", self.suservo_channel, new_setpoint
            )
            self.core.break_realtime()
        self.suservo_channel.set_dds_offset(
            profile=self.suservo_profile, offset=self.setpoint_to_offset(new_setpoint)
        )

    @kernel
    def set_channel_state(self, rf_switch_state: TBool, enable_iir: TBool):
        """
        Quickly enable / disable the RF switch and servo. This method does not
        advance the timeline.
        """
        # Enable this channel
        self.suservo_channel.set(
            en_out=(1 if rf_switch_state else 0),
            en_iir=(1 if enable_iir else 0),
            profile=self.suservo_profile,
        )

    @kernel
    def set_iir_params(self, kp=0.0, ki=DEFAULT_KI, gain_limit=0.0, delay=0.0):
        """
        Set loop filter parameters for the suservo. See ARTIQ documentation for
        details. Note that gains should usually be negative.
        """
        if self.debug_enabled:
            logger.info(
                "Setting iir params for %s: profile= %s, sampler_channel=%s, kp=%s, ki=%s, gain_limit=%s, delay=%s",
                self.suservo_channel,
                self.suservo_profile,
                self.sampler_channel,
                kp,
                ki,
                gain_limit,
                delay,
            )
            self.core.break_realtime()

        self.suservo_channel.set_iir(
            self.suservo_profile, self.sampler_channel, kp, ki, gain_limit, delay
        )
