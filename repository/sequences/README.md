# Sequence architecture and design intent

This document describes the direction in which the experiment code should converge.
It is deliberately more authoritative than inferring a design from the existing Python
files: those files contain several generations of ideas, and an older pattern may still
be present because it runs useful experiments rather than because it is the pattern to
copy.

The immediate reference implementations are `repository/experiments/atoms/cs_mot.py`,
`rb_mot.py`, and the composed `rb_cs_mot.py`. The one current apparatus owner and the
standard experiment lifecycle both live in
`sequences/parts/lab_hardware.py`; species settings, stages, and composed operations
live in `sequences/parts/cs_mot.py` and `sequences/parts/rb_mot.py`; shared camera,
live-slot, ROI, and occupation-statistics behaviour lives in
`sequences/parts/imaging.py`. Older implementations are retained under
`sequences/unused/` as behavioural and historical references, but are not patterns to
copy into new experiments.

## What we want experiment code to feel like

Most experiments should define one complete **shot** by composing reusable operations:

```python
load_rb_mot_to_tweezers()
load_cs_mot_to_tweezers()
do_my_experiment()
cool_and_image_atoms()
```

The real implementation will use fragments and methods rather than necessarily these
exact free functions, but the top-level shot should read at approximately this level.
An experimentalist should be able to understand its chronological structure without
first reading the device drivers.

The same shot can then be placed inside a repeat scan to obtain statistics, and that
statistical fragment can in turn be scanned or optimised over any parameters exposed by
the shot's constituent stages.

The main goals are:

- Experimental operations are composable and readable in chronological order.
- Every experimentally meaningful setting can be exposed as an ndscan parameter.
- A physical device is not independently reimplemented by every stage that uses it.
- State assumptions between operations are explicit and local.
- Initialisation, shot boundaries, pauses, failures, and final safe-off behaviour have
  well-defined semantics.
- Reusable code has one canonical home in `repository/sequences/parts`.

## Vocabulary and layers

Keeping the following concepts separate prevents one fragment from becoming a mixture
of device driver, parameter namespace, state machine, and complete experiment.

### Hardware capability

The current `LabRTIOHardware` fragment owns all RTIO hardware which our present family
of shots may use. Its name marks a deliberate boundary: host-side controllers,
datasets, and analysis do not belong to it. This follows the real apparatus lifecycle:
initialising one Zotino or SU-Servo channel has board-wide consequences, and
experiments do not run concurrently in the lab. It declares the device handles once
and provides kernel-safe actions where they add hardware knowledge, such as:

```text
program_rb_light(...)
turn_rb_light_on()
set_fields_with_quad_demand(...)
set_rb_tweezer_setpoint(...)
set_cs_tweezer_setpoint(...)
```

The hardware owner contains **no ndscan parameters**. Its methods
accept explicit values supplied by the experimental layer. This prevents the physical
hardware object from deciding which of several MOT, molasses, cooling, or imaging
settings is currently meaningful.

Not every device operation needs a forwarding method. A stage may directly call a
public handle on the borrowed owner when the action is one transparent primitive:

```python
self.hardware.ttl_quad.off()
self.hardware.ttl_camera_exposure.on()
```

Keep a hardware helper when it adds calibration, profile/channel selection,
multi-device coordination, timing, atomic updates, safety ordering, or another
non-obvious invariant. For example, `set_cs_tweezer_setpoint()` converts volts to the
correct SU-Servo offset and selects the 1066 nm channel/profile, while
`turn_cs_light_on()` coordinates shutters, a prefire delay, and two RF switches.

Both tweezer channels are therefore initialised with zero demand. Their MOT and
cooling/imaging stages each own a `tweezer_setpoint` parameter and program it when
entering that stage. Equal defaults do not couple those parameters. The shared RTIO
methods only convert the requested voltage using the calibrated 10.24 V SUServo DAC
full scale.

Parts receive this same owner directly as an ordinary, non-owning Python reference.
Holding that reference is not hardware ownership: the part does not call
`setattr_device()`, initialise a board, or independently clean it up. Explicit species
names make non-trivial calls easy to understand and search for, without a second layer
which only forwards or renames hardware operations.

A part should still call only the operations needed to do its job.  This is a coding
rule rather than an interface restriction; in this small shared codebase the simpler
instruction is more valuable than enforcing permissions through many small APIs.

Safety constants and electrical configuration are not scan parameters. For example,
"quad demand is zero outside the MOT" is an invariant, not a parameter which every
stage happens to default to zero.

### Atom imaging

`AtomImageReadout` is deliberately separate from `LabRTIOHardware`.
`LabRTIOHardware` owns the deterministic RTIO apparatus and its common safe state;
`AtomImageReadout` owns a host-side camera controller, mutable live datasets,
image-processing results, and statistical interpretation. Those resources have a
different lifecycle and cannot be manipulated inside an RTIO kernel.

The exposure TTL remains on `LabRTIOHardware`, because asserting it at a precise point
in the shot is real-time hardware control. `AtomImageReadout` prepares the camera once
and leaves its run-till-abort acquisition armed across shots. Imaging stages emit
their exposure TTLs without intervening host calls; afterwards, `AtomImageReadout`
drains the ordered frames from the SDK circular buffer and processes them.

Each shot owns exactly one `AtomImageReadout` fragment and declares its expected image
slots at build time. The fixed sensor crop and camera readout settings are shared;
analysis ROI positions and thresholds are defaults for each slot and remain mutable
through `live.imaging` while the experiment runs.

Live, mutable state and archived ndscan results deliberately use different shapes of
name:

```text
live.imaging.slot0.image
live.imaging.slot0.rois
live.imaging.slot0.thresholds
live.imaging.slot0.valid

image_readout/tweezer_image
image_readout/tweezer_roi_counts
image_readout/tweezer_roi_bright
image_readout/tweezer_roi_thresholds_applied
```

Additional image slots use species- or purpose-qualified singular stems, for example
`rb_tweezer_image` and `cs_tweezer_image`. Statistics use the same stem but are
published by the repeat wrapper, such as `rb_tweezer_average_bright_probability`.

`make_repeated_image_shot_statistics()` is the standard way to turn a complete shot
into one statistical scan point. It runs the shot as a no-axis prepared child scan,
lets `AtomImageReadout` reduce every registered binary occupation stream, and copies
the resulting probabilities into parent-owned result channels. The copy is necessary
because a detached child scan intentionally owns a separate ndscan result site; the
statistics are calculated only once.

An `AtomImageReadout` may also receive named `ConditionalProbability` declarations.
Their `event` and `given` expressions use the zero-based dnamic-toolkit condition
language. For each shot and ROI group, `given` decides whether the shot enters the
denominator, while `given & event` decides whether it enters the numerator. Saved
outputs include probabilities, errors, selected counts and successful counts, both per
group and pooled. This makes a value such as survival auditable even when few initially
loaded shots satisfy its condition.

### Settings or profile

A settings fragment contains the parameters meaningful for one experimental context,
for example MOT cooling frequency and amplitude or molasses shim values. It should not
directly own the physical device merely because it owns values eventually sent to that
device.

It is correct for several profiles to contain parameters which drive the same hardware.
MOT and molasses cooling frequencies are distinct experimental quantities and should
remain independently scannable even though one DDS produces both.

### State recipe

A state recipe coordinates several hardware capabilities to establish a named state.
Two recipes are especially important:

- **Safe state:** suitable for relinquishing hardware to another experiment. Shutters
  and RF switches are off, triggers are low, servos are disabled as appropriate, and
  dangerous demands are zeroed.
- **Repeatable shot state:** the deterministic boundary between successful shots. It
  need not be globally safe; it may retain expensive programming or benign analogue
  demands when doing so makes repeated acquisition faster.

These recipes should also normally contain no scan parameters. If preparation depends
on a shot parameter, that value belongs to the shot or stage and is passed explicitly.

For the first implementation, the repeatable shot state is deliberately the same as
the safe state. This is simple, conservative, and gives every shot an unambiguous
starting point. The lifecycle should nevertheless call this through a conceptually
separate `before_shot`/shot-boundary operation. If full safe-off later proves too slow,
a different state-recipe fragment can be supplied for that role without changing the
shot, its statistical wrapper, or the final safe cleanup.

### Stage and transition

A stage combines settings with the capabilities needed to apply them. Its method name
and docstring must make its state contract clear.

A stage's `run(...)` method executes the complete timed interval and consumes all
parameters owned by that stage, including `duration`. A composition chooses the order
of its children but should not call `delay(child.duration.use())` on their behalf.
Parameter-only `*Settings` fragments are the deliberate exception: the containing
stage consumes their values.

RTIO outputs are latched. Returning from `run(...)` does not restore the state which
existed before the call, and a `delay(...)` only advances the RTIO cursor. In this
codebase, `run(...)` therefore means “establish this stage and spend its duration
there”, not “temporarily apply this stage and clean it up afterwards”. Any cleanup or
transition at the end must be explicit.

Small non-scannable composition choices are explicit boolean arguments, passed by
keyword:

```python
self.cooling.run(
    turn_light_on=False,
    turn_light_off=False,
)
```

This makes the relevant hardware state visible at the higher-order call site without
creating a separate method for every valid combination. Use a directional name such
as `run_from_dark_hold()` only when the alternative is a substantially different
transition which cannot be expressed clearly as a small set of orthogonal options.

Hidden assumptions are still not acceptable. A stage or transition docstring should
state:

```text
Requires: what may be assumed on entry.
During: what state is established for the timed interval.
Leaves: what remains latched after the method returns.
```

The `Leaves` section should explicitly say whether light, servos, fields, enables, and
trigger TTLs remain on or off. It is often useful to finish with “No previous hardware
state is restored automatically.”

### Part

A part is a reusable experimental operation built from stages or other parts, such as
`LoadCsMOTToTweezers` or `CoolAndImageCsAtoms`. Parts belong in
`repository/sequences/parts`, not in every experiment file that uses them.

A part may expose parameters through its child settings, but it should not own a second
copy of hardware already owned by the shot environment.

### Shot and experiment wrapper

A shot composes parts into one complete measurement and publishes the raw result of
that measurement. It should have a documented starting boundary and a documented
successful postcondition.

Wrappers add repetition, statistics, scans, optimisation, and dashboard presentation.
These mechanisms should not have to know the internal hardware sequence. Conversely,
the base shot should not need to know how many times a higher-level wrapper will repeat
it.

## Hardware lifecycle

The intended lifecycle for an uninterrupted scheduling tenure is:

```text
experiment starts or resumes
    initialise the hardware claimed by this experiment
    enter safe state

for each shot
    establish the shot boundary (initially the same safe state)
    run one shot

experiment finishes, fails, or pauses
    enter safe state
    relinquish the hardware
```

For prepared on-core ndscan experiments, `device_setup()` is called before each
`run_once()`, while `device_cleanup()` is called as the scan kernel is left, including
at a pause. A shared lifecycle part should encode the policy above so individual
experiments do not each reproduce it.

On the first `device_setup()` following `host_setup()`, the lifecycle part can
initialise hardware and enter the shot-boundary state. Later calls restore that same
boundary before each shot. Initially this operation simply enters the safe state.
`device_cleanup()` always enters the safe state, regardless of which shot-boundary
recipe is selected in the future. A resume creates a new tenure and repeats the initial
sequence.

The repeatable-state operation must tolerate every documented successful shot
postcondition. If a shot fails and the kernel is left, the safe cleanup is the recovery
path. Retry behaviour should be considered explicitly when retries are enabled.

Host-side resources have the analogous lifecycle. For example, camera configuration
belongs at session entry, while aborting an acquisition belongs in host cleanup as well
as in error handling around a shot.

## Ownership and dependency rules

"Ownership" here means responsibility for lifecycle, not merely having a Python
reference to an ARTIQ device.

- The current apparatus hardware has one lifecycle owner within an experiment:
  `LabEnvironment.hardware`.
- Parts and stages receive a non-owning reference to that same `LabRTIOHardware`
  instance.
- Only `LabLifecycle` coordinates initialisation, between-shot preparation, and final
  safe cleanup.
- Parts call the explicit hardware methods they need and do not initialise or clean up
  the apparatus themselves.
- Safe cleanup covers every resource represented by `LabRTIOHardware`. Extend that
  class's initialisation and safe recipe when another board or channel becomes part of
  the apparatus used by these shots.

Passing the one owned hardware instance as a build argument is the standard convention
in these sequences. It is a shared reference, not a second registration or lifecycle.

## How to compartmentalise hardware

Use one lifecycle owner and keep its implementation navigable by grouping methods into
clearly labelled sections. Group operations by physical transaction and safety
boundary rather than blindly by logical stage or individual channel. A sensible
conceptual MOT decomposition is:

- Cooling/repump light: the two DDS channels, their shared CPLD, RF switches, and
  shutters.
- Low-field outputs: the three shim DAC channels, quad demand DAC channel, and quad
  enable TTL.
- Tweezer servo: the SU-Servo channel and the configuration required to operate it.
- Camera trigger: the exposure TTL; host-side camera control can be a neighbouring
  capability.

The low-field capability deliberately includes shims and quad demand. Although they
are logically different quantities, they share one Zotino update and often need to
change atomically. Splitting them would make the logical API tidier at the expense of
correct hardware control.

### A possible later hardware split

Do not introduce this split merely for symmetry. The current single
`LabRTIOHardware` object is intentionally the simplest implementation, and the shared
`hardware` reference gives us a clean seam for extracting it later. Extraction becomes
worthwhile only when the class is genuinely difficult to navigate or another operation
needs a board-wide invariant which is hard to express safely in the current layout.

If that happens, divide ownership at shared initialisation and update boundaries rather
than mechanically making one wrapper per physical board. A likely structure is:

```text
LabEnvironment
├── hardware: LabRTIOHardware
│   ├── zotino_outputs: ZotinoOutputs       (owns zotino0)
│   ├── tweezer_servo: TweezerSUServo       (owns the SU-Servo devices)
│   ├── laser_dds: LaserDDSOutputs           (owns the cooling-light DDSs)
│   └── digital_outputs: DigitalOutputs      (owns the relevant TTLs)
├── low_fields: LowFieldControl              (borrows ZotinoOutputs)
├── rb_light: RbLightControl                 (borrows DDS and TTL owners)
└── cs_light: CsLightControl                 (borrows DDS and TTL owners)
```

Each RTIO device would still be declared by exactly one owner. Logical controls would
hold non-owning references to one or more owners and add meaningful coordinated
behaviour or safety invariants; they should not exist merely to rename and forward
methods. For example, `LowFieldControl.set_bias_fields()` could force quad demand to
zero, while `set_mot_fields()` would be the only operation accepting a non-zero quad
demand. Both could still ask `ZotinoOutputs` to update all four DAC channels in one
physical transaction.

A parameterised stage using those logical controls would remain straightforward:

```python
def build_fragment(self, low_fields, cs_light, tweezers):
    self.low_fields = low_fields
    self.cs_light = cs_light
    self.tweezers = tweezers

@kernel
def enter(self):
    self.low_fields.set_mot_fields(
        self.ew.use(), self.ns.use(), self.ud.use(), self.quad.use()
    )
    self.cs_light.program_and_turn_on(
        self.cool_frequency.use(),
        self.repump_frequency.use(),
        self.cool_amplitude.use(),
        self.repump_amplitude.use(),
    )
    self.tweezers.set_cs_depth(self.tweezer_setpoint.use())
```

The top-level experiment would still compose named stages such as
`load_cs_mot_to_tweezers.run()`. Only the implementation and build-time references of
those stages would change, so this refactor does not need to be performed pre-emptively.

`LabEnvironment` owns the apparatus and the one general lifecycle. It intentionally
does not expose species-specific forwarding views. Stages all receive
`LabEnvironment.hardware` and should use only the operations they need:

```text
MOT        -> light, fields, tweezer
molasses   -> light, fields
cooling    -> light, fields, tweezer
imaging    -> light, camera trigger
```

## What belongs in `sequences/parts`

The parts directory is intended to become the canonical library for:

- Parameter-free hardware capabilities.
- Scoped initialisation and safe-state recipes.
- Repeatable shot-boundary recipes.
- Parameter/profile fragments which are genuinely reused.
- Stages with explicit state contracts.
- Composed operations such as MOT loading, cooling, rearrangement, and imaging.
- Generic shot/statistics wrappers where their semantics are independent of one
  particular experiment.

Runnable recipes belong in `repository/experiments/atoms` or
`repository/experiments/no_atoms`: they specify which parts are composed, which
results are published, and which scan or optimisation request is offered to the
dashboard. `repository/sequences` contains the reusable parts and archived
implementations rather than being a mixed entry-point directory.

`unused/parts/initialiser.py` is an older whole-device-database discovery approach.
Its broad `safe_off()` operation is useful historical context and may remain useful as
an explicit lab-wide reset experiment. The composable-shot path instead uses an
explicit `LabRTIOHardware` list: broad enough to initialise the current apparatus
coherently, but small and readable enough that every output and safe value is
deliberate.

## Parameter rules

- Put a parameter at the first layer where it is experimentally meaningful.
- A stage must consume every parameter it owns during its complete `run(...)`.
  Transition-only settings belong to the composition which performs that transition,
  rather than remaining as unused parameters on other instances of the stage.
- Do not introduce parameters solely to make a generic hardware method convenient.
- Do not make safety values scannable.
- Keep independently meaningful stage values independent, even when they share a
  device.
- If two stages intentionally share one value, express that sharing once in the
  composition or parameter mapping rather than relying on equal defaults.
- Give stage-specific profiles distinct classes or stable identities so ndscan labels
  and saved metadata remain unambiguous.

## Reading existing code

When consulting older code:

- Treat working legacy sequences as behavioural references, especially for device
  ordering, timing, and safety details.
- Treat the recipes in `repository/experiments/atoms/` and their corresponding
  sequence-parts modules as the current architecture.
- Do not assume that a pattern is recommended merely because it occurs in several old
  files; duplication may reflect their shared history.
- When replacing a working implementation, preserve its RTIO behaviour first and make
  experimental changes separately.

## Migration plan

1. The shared lifecycle part now implements initial-safe, before-shot, and final-safe
   transitions.  All three currently use the same safe-state recipe, while
   `before_shot()` remains the explicit extension point.
2. `LabEnvironment` now owns the current Rb/Cs hardware once. Species settings, stages,
   and operations receive that hardware by non-owning reference; they remain separate
   modules and are not forced through a symmetric experimental implementation.
3. The current Cs and Rb experiments are thin consumers of those parts and the shared
   `AtomImageReadout` fragment. `repository/experiments/atoms/rb_cs_mot.py`
   demonstrates sequential composition with one fixed camera configuration and two
   image slots. Continue
   validating scans, nested shot repetition, failure cleanup, and scheduler
   pause/resume on hardware.
4. The superseded monolithic Cs and Rb implementations are retained as
   `unused/cs_mot_monolith.py` and `unused/rb_mot_monolith.py`.
5. Extract further common physical capabilities only where that reduces duplication
   without hiding important hardware differences.
6. Consider an ndscan non-owning fragment-reference API once the desired usage has
   been demonstrated locally.

The end goal is not the smallest possible number of classes. It is one obvious place
for each hardware action, state contract, experimental parameter, and reusable
operation—and top-level shots which remain easy to read.
