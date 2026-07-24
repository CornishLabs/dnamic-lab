# Atom experiments

These are the current runnable atom-shot recipes. They compose reusable operations
from `repository/sequences/parts/` and use the shared `LabEnvironment` lifecycle.

An atom experiment should normally assume the standard optical and electrical
configuration. If it instead requires a manual hardware change, its module docstring
must include `Physical setup required` and `Restoration required` sections just like a
no-atom experiment.
