"""Small helpers for turning a complete shot into one statistical scan point.

An atom experiment normally has two useful levels:

* the *shot* emits raw results such as one binary occupation array per image;
* the outer experiment repeats that shot and publishes probabilities which can be
  plotted or optimised as ordinary result channels.

ndscan deliberately gives a prepared child scan its own result namespace.  The class
factory below contains the otherwise repetitive plumbing which copies the child's
analysis outputs into its parent point.  It does not know how image statistics are
calculated; that remains the responsibility of the shot's ``image_readout`` fragment.
"""

from ndscan.define.fragment import ExpFragment
from ndscan.define.parameters import IntParam, IntParamHandle
from ndscan.runtime.api import (
    ExecutionPolicy,
    ScanRequest,
    setattr_prepared_child_scan,
)


DEFAULT_MAX_SHOTS_PER_BATCH = 16


def make_repeated_image_shot_statistics(
    shot_class,
    *,
    default_shots_per_point=50,
    shots_description="Complete shots averaged into one result point",
    max_shots_per_batch=DEFAULT_MAX_SHOTS_PER_BATCH,
    class_name=None,
):
    """Return an :class:`ExpFragment` which repeats and summarises ``shot_class``.

    ``shot_class`` must expose an ``image_readout`` attribute whose default analysis
    produces the statistics declared by ``make_statistics_channels()``.  Keeping that
    small convention means a sequence only has to define its physical shot; this
    factory supplies the standard no-axis repeat scan around it.

    The generated fragment still exposes ``shot`` and ``shots_per_point``.  A caller
    may therefore subclass it to add an objective or customise the dashboard without
    losing access to the underlying shot parameters.
    """

    if default_shots_per_point < 1:
        raise ValueError("default_shots_per_point must be at least one")
    if max_shots_per_batch < 1:
        raise ValueError("max_shots_per_batch must be at least one")

    generated_name = class_name
    if generated_name is None:
        generated_name = f"{shot_class.__name__.removesuffix('Shot')}Statistics"

    class RepeatedImageShotStatistics(ExpFragment):
        """Repeat one complete image-producing shot into one statistical point."""

        def build_fragment(self):
            # A prepared child scan owns the shot's setup, execution, cleanup and
            # nested result site.  setattr_prepared_child_scan() both constructs and
            # detaches the shot so the parent does not also traverse it normally.
            self.repeat_scan = setattr_prepared_child_scan(
                self,
                "shot",
                shot_class,
                scan_name="repeat_scan",
                max_rtio_underflow_retries=0,
            )

            self.shots_per_point = self.setattr_param(
                "shots_per_point",
                IntParam,
                shots_description,
                default_shots_per_point,
                min=1,
            )
            self.shots_per_point: IntParamHandle

            # These are parent-owned copies of the child's analysis outputs.  No
            # statistics are calculated here: the declarations merely let each
            # finished child scan become a normal y-value of a higher-level scan.
            self._statistics_channels = (
                self.shot.image_readout.make_statistics_channels(self.setattr_result)
            )

        def publish_derived_statistics(self, outputs):
            """Hook for objectives derived from the standard image statistics.

            Subclasses may override this to publish a loading rate, survival score,
            or another experiment-specific scalar.  ``outputs`` is the same named
            result mapping returned by the repeated child scan, so the underlying
            probabilities never need to be calculated a second time.
            """

        def run_once(self):
            num_shots = int(self.shots_per_point.get())
            self.repeat_scan.configure(
                ScanRequest.single(
                    execution_policy=ExecutionPolicy(
                        max_points_per_batch=min(
                            num_shots,
                            max_shots_per_batch,
                        )
                    )
                ).with_repeats(repeats=num_shots)
            )

            # execute() runs the child's default no-axis analysis once after all
            # repetitions.  Its returned values are copied, not recalculated.
            outputs = self.repeat_scan.execute()
            for name, channel in self._statistics_channels.items():
                channel.push(outputs[name])
            self.publish_derived_statistics(outputs)

    # A useful FQN makes dashboard parameter paths and error messages refer to the
    # concrete experiment rather than to a generic class nested inside this factory.
    RepeatedImageShotStatistics.__name__ = generated_name
    RepeatedImageShotStatistics.__qualname__ = generated_name
    RepeatedImageShotStatistics.__module__ = shot_class.__module__
    RepeatedImageShotStatistics.__doc__ = (
        f"Repeat {shot_class.__name__} and publish one statistical point."
    )
    return RepeatedImageShotStatistics
