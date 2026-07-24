"""Minimal example of binding a transition to the stages on either side.

The experiment has three independently adjustable quantities:

* the Cs tweezer setpoint used by stage A;
* the Cs tweezer setpoint used by stage B;
* the duration of the linear A-to-B ramp.

``TweezerRampAtoB`` also declares start and end parameters because it is a reusable
part which needs two inputs.  The composition binds those inputs to the corresponding
stage parameters, so they are not independent dashboard controls.

This is an intentionally small hardware example.  It changes the real Cs 1066 nm
tweezer-servo setpoint, holds briefly at either end so the sequence is easy to see on
an oscilloscope, and returns the shared apparatus to its safe state afterwards.
"""

from artiq.coredevice.core import Core
from artiq.experiment import kernel
from artiq.language.core import delay
from artiq.language.units import V, ms, s, us

from ndscan.define.fragment import ExpFragment
from ndscan.define.parameters import FloatParam, FloatParamHandle
from ndscan.define.result_channels import FloatChannel
from ndscan.runtime.api import make_fragment_prepared_dashboard_scan_exp

from repository.sequences.parts.lab_hardware import (
    LabEnvironment,
    UsesLabRTIOHardware,
)


STAGE_A_SETPOINT = 5.0 * V
STAGE_B_SETPOINT = 6.0 * V
RAMP_DURATION = 20.0 * ms

# The update period is an implementation detail rather than an experimental
# coordinate in this example.  The last update is still placed exactly at the
# requested ramp duration, even when that duration is not a multiple of this value.
RAMP_UPDATE_PERIOD = 100.0 * us
ENDPOINT_HOLD_TIME = 10.0 * ms


class _TweezerSetpointStage(UsesLabRTIOHardware):
    """Set one parameterised Cs tweezer-servo target."""

    LABEL: str
    DEFAULT_SETPOINT: float

    def build_fragment(self, hardware):
        self._use_hardware(hardware)
        self.tweezer_setpoint = self.setattr_param(
            "tweezer_setpoint",
            FloatParam,
            f"Cs tweezer setpoint during stage {self.LABEL}",
            self.DEFAULT_SETPOINT,
            min=0.0 * V,
            max=10.0 * V,
            unit="V",
        )
        self.tweezer_setpoint: FloatParamHandle

    @kernel
    def run(self):
        """Set the target and leave the enabled servo at that target."""
        self.hardware.set_cs_tweezer_setpoint(self.tweezer_setpoint.use())


class TweezerStageA(_TweezerSetpointStage):
    """First parameterised tweezer stage."""

    LABEL = "A"
    DEFAULT_SETPOINT = STAGE_A_SETPOINT


class TweezerStageB(_TweezerSetpointStage):
    """Second parameterised tweezer stage."""

    LABEL = "B"
    DEFAULT_SETPOINT = STAGE_B_SETPOINT


class TweezerRampAtoB(UsesLabRTIOHardware):
    """Linearly ramp the Cs tweezer target between two supplied endpoints.

    In isolation, ``start_setpoint`` and ``end_setpoint`` are ordinary parameters.
    A composition is expected to bind them to the state parameters on either side.

    Requires:
        The Cs tweezer servo is enabled, normally at ``start_setpoint``.

    Guarantees:
        The servo target is ``end_setpoint`` and the RTIO cursor has advanced by
        exactly ``duration``.
    """

    def build_fragment(self, hardware):
        self._use_hardware(hardware)

        # These parameters describe the interface of the reusable transition.  They
        # disappear as independent dashboard values after BoundStageTransitionShot
        # binds them to the two neighbouring stages below.
        self.start_setpoint = self.setattr_param(
            "start_setpoint",
            FloatParam,
            "Cs tweezer ramp start setpoint",
            STAGE_A_SETPOINT,
            min=0.0 * V,
            max=10.0 * V,
            unit="V",
        )
        self.start_setpoint: FloatParamHandle

        self.end_setpoint = self.setattr_param(
            "end_setpoint",
            FloatParam,
            "Cs tweezer ramp end setpoint",
            STAGE_B_SETPOINT,
            min=0.0 * V,
            max=10.0 * V,
            unit="V",
        )
        self.end_setpoint: FloatParamHandle

        # Duration remains free and independently scannable: it is a genuine
        # property of the transition rather than either adjacent stage.
        self.duration = self.setattr_param(
            "duration",
            FloatParam,
            "Duration of the Cs tweezer A-to-B ramp",
            RAMP_DURATION,
            min=0.0 * ms,
            max=1.0 * s,
            unit="ms",
        )
        self.duration: FloatParamHandle

    @kernel
    def run(self):
        start = self.start_setpoint.use()
        end = self.end_setpoint.use()
        duration = self.duration.use()

        if duration <= 0.0 * s:
            # A zero-duration transition is an immediate step to the final state.
            self.hardware.set_cs_tweezer_setpoint(end)
            return

        # Use approximately RAMP_UPDATE_PERIOD between updates.  Recomputing the
        # actual interval from the integer number of steps makes their sum exactly
        # equal to the requested duration.
        num_steps = int(duration / RAMP_UPDATE_PERIOD)
        if num_steps < 1:
            num_steps = 1
        step_duration = duration / num_steps

        # Reasserting the expected starting value is harmless when stage A met its
        # contract, and makes the transition's first programmed value explicit.
        self.hardware.set_cs_tweezer_setpoint(start)

        for step in range(1, num_steps + 1):
            delay(step_duration)
            fraction = step / num_steps
            setpoint = start + fraction * (end - start)
            self.hardware.set_cs_tweezer_setpoint(setpoint)


class BoundStageTransitionShot(ExpFragment):
    """Compose stage A, its bound transition, and stage B."""

    def build_fragment(self):
        # Prepared kernel fragments must declare their core directly.
        self.setattr_device("core")
        self.core: Core

        # LabEnvironment is the single owner of hardware initialisation and safety.
        self.setattr_fragment("environment", LabEnvironment)
        self.environment: LabEnvironment

        self.setattr_fragment(
            "stage_a",
            TweezerStageA,
            hardware=self.environment.hardware,
        )
        self.stage_a: TweezerStageA

        self.setattr_fragment(
            "a_to_b",
            TweezerRampAtoB,
            hardware=self.environment.hardware,
        )
        self.a_to_b: TweezerRampAtoB

        self.setattr_fragment(
            "stage_b",
            TweezerStageB,
            hardware=self.environment.hardware,
        )
        self.stage_b: TweezerStageB

        # These are identity bindings: all three handles now read the same two
        # underlying parameter stores.  start_setpoint and end_setpoint cease to be
        # free parameters, so the dashboard offers only the stage values themselves.
        self.a_to_b.bind_param(
            "start_setpoint",
            self.stage_a.tweezer_setpoint,
        )
        self.a_to_b.bind_param(
            "end_setpoint",
            self.stage_b.tweezer_setpoint,
        )

        self.reached_setpoint = self.setattr_result(
            "reached_setpoint",
            FloatChannel,
            "Programmed tweezer setpoint at the end of the sequence",
            unit="V",
        )

    def get_always_shown_params(self):
        # Listing these explicitly also documents the public controls of the
        # composition.  The two bound ramp endpoints are deliberately absent.
        return [
            self.stage_a.tweezer_setpoint,
            self.stage_b.tweezer_setpoint,
            self.a_to_b.duration,
        ]

    @kernel
    def run_once(self):
        self.core.break_realtime()
        delay(1.0 * ms)

        # The lifecycle supplied a safe state with the servo disabled.  Enable it
        # once; the three composed operations only change its target.
        self.environment.hardware.set_cs_tweezer_servo_enabled(1)

        self.stage_a.run()
        delay(ENDPOINT_HOLD_TIME)

        self.a_to_b.run()

        # Stage B reasserts the same endpoint reached by the ramp.  This should not
        # produce a jump, and demonstrates that B remains independently usable.
        self.stage_b.run()
        delay(ENDPOINT_HOLD_TIME)

        final_setpoint = self.stage_b.tweezer_setpoint.use()
        self.environment.hardware.set_safe()
        self.reached_setpoint.push(final_setpoint)


BoundStageTransitionExample = make_fragment_prepared_dashboard_scan_exp(
    BoundStageTransitionShot,
    max_rtio_underflow_retries=0,
)
