"""Sequentially load Rb and Cs, then cool and image each species.

The chronological kernel is intentionally short.  Hardware ownership and safety live
in ``LabEnvironment``; species settings and transitions live in the parts modules; and
the common camera/ROI/statistics plumbing lives in ``AtomImageReadout``.
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

from repository.sequences.parts.cs_mot import (
    CoolAndImageCsAtoms,
    LoadCsMOTToTweezers,
)
from repository.sequences.parts.imaging import (
    DUAL_CS_TWEEZER_IMAGE,
    DUAL_RB_TWEEZER_IMAGE,
    AtomImageReadout,
)
from repository.sequences.parts.lab_hardware import LabEnvironment
from repository.sequences.parts.rb_mot import (
    CoolAndImageRbAtoms,
    LoadRbMOTToTweezers,
)


SHOTS_PER_POINT = 50


class LoadRbThenCsAndImageBoth(ExpFragment):
    """Load Rb then Cs into tweezers, followed by separate Rb and Cs images."""

    def build_fragment(self):
        # Prepared ndscan kernels require the root fragment to declare its core even
        # though the same physical device is also owned by the global environment.
        self.setattr_device("core")
        self.core: Core

        self.setattr_fragment("environment", LabEnvironment)
        self.environment: LabEnvironment

        self.setattr_fragment(
            "load_rb",
            LoadRbMOTToTweezers,
            hardware=self.environment.hardware,
        )
        self.load_rb: LoadRbMOTToTweezers
        self.setattr_fragment(
            "load_cs",
            LoadCsMOTToTweezers,
            hardware=self.environment.hardware,
        )
        self.load_cs: LoadCsMOTToTweezers

        self.setattr_fragment(
            "readout_rb",
            CoolAndImageRbAtoms,
            hardware=self.environment.hardware,
        )
        self.readout_rb: CoolAndImageRbAtoms
        self.setattr_fragment(
            "readout_cs",
            CoolAndImageCsAtoms,
            hardware=self.environment.hardware,
        )
        self.readout_cs: CoolAndImageCsAtoms

        self.setattr_fragment(
            "image_readout",
            AtomImageReadout,
            slots=(DUAL_RB_TWEEZER_IMAGE, DUAL_CS_TWEEZER_IMAGE),
        )
        self.image_readout: AtomImageReadout

    def get_default_analyses(self):
        # ndscan deliberately asks only the scanned ExpFragment for analyses; ordinary
        # child fragments are not traversed implicitly.  The camera part owns the
        # calculation, while this one-line delegation exposes it to scans of the shot.
        return self.image_readout.get_default_analyses()

    @kernel
    def load_both_species(self):
        """Load sequentially, leaving both trap servos on and resonant light off."""
        self.load_rb.run_to_dark_hold()
        # run_to_dark_hold() leaves 817 nm on while closing the Rb light shutters.
        self.load_cs.run_to_dark_hold()

    @kernel
    def run_once(self):
        self.image_readout.begin_shot()
        self.core.break_realtime()
        delay(20.0 * ms)

        self.load_both_species()

        # The continuously armed camera places these two exposures into its circular
        # buffer in order.  No host RPC interrupts the RTIO sequence between them.
        self.readout_rb.run_from_dark_hold()
        self.readout_cs.run_from_dark_hold()

        # Both exposures are complete, so make the full apparatus safe before draining
        # and processing both buffered images.  Lifecycle cleanup repeats this safely.
        self.environment.hardware.set_safe()
        self.image_readout.wait_read_all()


LoadRbThenCsAndImageBothExp = make_fragment_prepared_dashboard_scan_exp(
    LoadRbThenCsAndImageBoth,
    max_rtio_underflow_retries=0,
)


class LoadRbThenCsAndImageBothStatistics(ExpFragment):
    """Repeat the dual-species shot and publish Rb and Cs loading probabilities."""

    def build_fragment(self):
        self.setattr_fragment("shot", LoadRbThenCsAndImageBoth, detached=True)
        self.shot: LoadRbThenCsAndImageBoth

        self.repeat_scan = prepare_child_scan(
            self,
            self.shot,
            name="repeat_scan",
            max_rtio_underflow_retries=0,
        )
        self.shots_per_point = self.setattr_param(
            "shots_per_point",
            IntParam,
            "Dual-species shots to average into one scan point",
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


class LoadRbThenCsAndImageBothStatisticsDashboard(LoadRbThenCsAndImageBothStatistics):
    """Keep the principal timing and trap-depth controls visible in the dashboard."""

    def get_always_shown_params(self):
        shown = super().get_always_shown_params()
        shown += [
            self.shots_per_point,
            self.shot.load_rb.mot.duration,
            self.shot.load_rb.mot.tweezer_setpoint,
            self.shot.load_cs.mot.duration,
            self.shot.load_cs.mot.tweezer_setpoint,
            self.shot.readout_rb.cooling.duration,
            self.shot.readout_rb.cooling.tweezer_setpoint,
            self.shot.readout_cs.cooling.duration,
            self.shot.readout_cs.cooling.tweezer_setpoint,
            self.shot.image_readout.camera_timeout,
        ]
        return shown


LoadRbThenCsAndImageBothStatisticsExp = make_fragment_prepared_dashboard_scan_exp(
    LoadRbThenCsAndImageBothStatisticsDashboard,
    max_rtio_underflow_retries=0,
)
