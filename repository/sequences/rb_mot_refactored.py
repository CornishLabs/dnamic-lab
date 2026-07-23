"""Rb MOT-to-tweezers shots, result publication, and scan recipes.

The reusable hardware, settings, stages, and experimental operations live in
``repository.sequences.parts.rb_mot``.  This file intentionally stays at the recipe
level: it composes those parts into a concrete camera shot and a repeated-shot
statistics wrapper.
"""

from artiq.coredevice.core import Core
from artiq.experiment import kernel
from artiq.language.core import delay
from artiq.language.units import ms

from ndscan.define.fragment import ExpFragment
from ndscan.define.parameters import IntParam, IntParamHandle
from ndscan.runtime.api import (
    ExecutionPolicy,
    ScanRequest,
    make_fragment_prepared_dashboard_scan_exp,
    prepare_child_scan,
)

from repository.sequences.parts.lab_hardware import LabEnvironment
from repository.sequences.parts.imaging import AtomImageReadout, RB_TWEEZER_IMAGE
from repository.sequences.parts.rb_mot import (
    CoolAndImageRbAtoms,
    LoadRbMOTToTweezers,
)


# -----------------------------------------------------------------------------
# Experiment default
# -----------------------------------------------------------------------------

SHOTS_PER_POINT = 50


# -----------------------------------------------------------------------------
# Complete shot recipe used as an ndscan experiment
# -----------------------------------------------------------------------------


class LoadRbMOTToTweezersImageRefactored(ExpFragment):
    """Run the refactored Rb load/cool/image sequence once."""

    def build_fragment(self):
        # The prepared runtime determines which core compiles ``run_once`` by looking
        # for this attribute directly on the ExpFragment.  The physical Core object is
        # the same one also used by ``self.environment.hardware``; this reference
        # does not create another hardware lifecycle owner.
        self.setattr_device("core")
        self.core: Core

        # The environment is the one lifecycle owner.  Before every shot it establishes
        # the shared safe boundary, and on scan exit/pause it returns there again.
        self.setattr_fragment("environment", LabEnvironment)
        self.environment: LabEnvironment
        self.setattr_fragment(
            "load_rb_mot_to_tweezers",
            LoadRbMOTToTweezers,
            hardware=self.environment.hardware,
        )
        self.load_rb_mot_to_tweezers: LoadRbMOTToTweezers
        self.setattr_fragment(
            "cool_and_image_atoms",
            CoolAndImageRbAtoms,
            hardware=self.environment.hardware,
        )
        self.cool_and_image_atoms: CoolAndImageRbAtoms
        self.setattr_fragment(
            "image_readout",
            AtomImageReadout,
            slots=(RB_TWEEZER_IMAGE,),
        )
        self.image_readout: AtomImageReadout

    def get_default_analyses(self):
        return self.image_readout.get_default_analyses()

    @kernel
    def rtio_events(self):
        self.core.break_realtime()
        delay(20.0 * ms)

        # The top-level sequence is now a readable composition of two reusable parts.
        self.load_rb_mot_to_tweezers.run()
        self.cool_and_image_atoms.run_from_molasses()

        self.environment.hardware.set_rb_tweezer_servo_enabled(0)
        delay(5.0 * ms)
        # Do not leave non-zero shim demands or configured outputs active while the
        # host waits for and processes the camera frame. Lifecycle cleanup repeats
        # this operation safely after run_once() returns.
        self.environment.hardware.set_safe()

    @kernel
    def run_once(self):
        self.image_readout.begin_shot()
        self.core.break_realtime()
        self.rtio_events()
        self.image_readout.wait_read_all()


LoadRbMOTToTweezersImageRefactoredExp = make_fragment_prepared_dashboard_scan_exp(
    LoadRbMOTToTweezersImageRefactored,
    max_rtio_underflow_retries=0,
)


# -----------------------------------------------------------------------------
# Repeated-shot statistics wrapper
# -----------------------------------------------------------------------------


class LoadRbMOTToTweezersImageStatisticsRefactored(ExpFragment):
    """Repeat the refactored shot and publish one probability point."""

    def build_fragment(self):
        self.setattr_fragment(
            "shot",
            LoadRbMOTToTweezersImageRefactored,
            detached=True,
        )
        self.shot: LoadRbMOTToTweezersImageRefactored

        self.repeat_scan = prepare_child_scan(
            self,
            self.shot,
            name="repeat_scan",
            max_rtio_underflow_retries=0,
        )

        self.shots_per_point = self.setattr_param(
            "shots_per_point",
            IntParam,
            "Raw shots to average into one scan point",
            SHOTS_PER_POINT,
            min=1,
        )
        self.shots_per_point: IntParamHandle

        self._stat_channels = self.shot.image_readout.make_statistics_channels(
            self.setattr_result
        )

    def run_once(self):
        num_shots = int(self.shots_per_point.get())
        self.repeat_scan.configure(
            ScanRequest.single(
                execution_policy=ExecutionPolicy(
                    max_points_per_batch=min(num_shots, 16)
                )
            ).with_repeats(repeats=num_shots)
        )
        outputs = self.repeat_scan.execute()
        for name, channel in self._stat_channels.items():
            channel.push(outputs[name])


class LoadRbMOTToTweezersImageStatisticsRefactoredDashboard(
    LoadRbMOTToTweezersImageStatisticsRefactored
):
    """Dashboard wrapper exposing the most frequently adjusted settings."""

    def get_always_shown_params(self):
        shown = super().get_always_shown_params()
        load = self.shot.load_rb_mot_to_tweezers
        readout = self.shot.cool_and_image_atoms
        shown += [
            self.shots_per_point,
            load.mot.duration,
            load.mot.tweezer_setpoint,
            load.molasses.duration,
            readout.cooling.duration,
            readout.cooling.tweezer_setpoint,
            readout.cooling.light.cool_frequency,
            readout.cooling.light.cool_dds_amp,
            readout.imaging.exposure_time,
            self.shot.image_readout.camera_timeout,
        ]
        return shown


LoadRbMOTToTweezersImageStatisticsRefactoredExp = (
    make_fragment_prepared_dashboard_scan_exp(
        LoadRbMOTToTweezersImageStatisticsRefactoredDashboard,
        max_rtio_underflow_retries=0,
    )
)
