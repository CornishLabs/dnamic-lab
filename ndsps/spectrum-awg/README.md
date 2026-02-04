# Spectrum AWG NDSP

This NDSP is used for programming the sequence mode of a Spectrum AWG card.
It uses the builder architechture of the `AWGSegmentFactory` library to pass a builder object around that can add segments anywhere in the ndscan fragment tree. This is then compiled and uploaded to the card before running the fragment tree.

#### A) Factory / builder (pure Python)

* `AWGProgramBuilder` and calibration transforms are **hardware-agnostic**.
* It consumes “physics-ish” ops (move in µm, etc.) via calibration objects and emits resolved IR.

This makes it usable:

* standalone (unit tests, notebooks)
* inside ndscan

#### B) ndscan fragment: “AWGProgrammerFragment” (host-side)

* Owns:

  * AWG connection / handle
  * last uploaded hashes
  * current uploaded program id / segment ids
* During ndscan point execution it does:

  * `prepare_point()` (host): resolve builder → IR, snap holds, compute segment hashes, upload only changed segments, set up sequence tables.
  * `device_setup()` or `run_once()` (kernel): arrange RTIO trigger events (TTL pulses) to advance segments / start playback.
  * `device_cleanup()` (kernel): safe state if needed.

Key: uploading is not RTIO-safe; it’s host work. Triggering is RTIO.

#### C) Passing the factory object through fragments (composability)

Fragments don’t share “global AWG state”; they *contribute* to the factory:

* Top fragment creates builder/context object once.
* Subfragments add segments/ops in **depth-first fragment tree order** (your requirement).
* This naturally determines segment insertion order without manual indexing.

Mechanically in ndscan:

* In `build_fragment()`, fragments declare they “use AWG” and get a reference to a shared builder (passed down or stored on parent).
* Each fragment has a method like `emit_awg(self, b: AWGProgramBuilder)` that appends its piece.
* The experiment’s `host_setup` / `prepare_point` assembles the full builder by walking fragments DFS.
