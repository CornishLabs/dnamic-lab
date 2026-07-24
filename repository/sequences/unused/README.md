# Archived sequences

These files are retained as behavioural and historical references. They predate the
current `LabEnvironment`, shared parts, stage-owned timing, and
`Requires`/`During`/`Leaves` state-contract conventions.

New runnable experiments should go under `repository/experiments/`; reusable
operations should use the modules under `repository/sequences/parts/`.

This directory remains inside the ARTIQ experiment repository, so some archived
experiments may still appear in the dashboard under `sequences/unused`. Their
experiment classes carry names such as `Monolith` where necessary to distinguish them
from the current implementations.
