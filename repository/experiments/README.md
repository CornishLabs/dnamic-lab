# Runnable experiments

Runnable laboratory entry points are divided by their physical context:

- `atoms/` contains complete shots and measurements involving trapped atoms;
- `no_atoms/` contains hardware calibration and characterisation experiments.

Reusable settings, hardware ownership, lifecycle policy, and stage implementations
remain in `repository/sequences/parts/`. The division here is about what the
experimentalist is running, not which devices a reusable part happens to control.

Every module which requires a manual hardware or optical-path change must say so near
the top of its module docstring under `Physical setup required`. It must also document
how to return the apparatus to its normal configuration under `Restoration required`.
Programmatic state deliberately left behind belongs under `Leaves hardware`.
