# SU-Servo (Sampler–Urukul Servo) mental model: FIR/IIR + control loops

This note is written **specifically in the context of ARTIQ’s SU-Servo**: an FPGA gateware module that:
- samples **Sampler** ADC channels at a fixed cadence,
- runs a small DSP controller per DDS channel,
- continuously writes **phase/frequency/amplitude** to **AD9910** DDS chips on **Urukul 4410** cards (in SU-Servo mode).

It’s meant to answer: “what is FIR/IIR here?”, “where is feedback?”, and “how do I write minimal experiments?”

---

## 1) The signals: x, o, y (and why “offset” is the setpoint)

SU-Servo’s DSP loop uses three key quantities:

- **x**: the **ADC measurement**, normalized to a “full-scale” unit range (conceptually `[-1, +1)`).
  - Physically it’s your photodiode voltage (or any analog signal) after the Sampler front-end and PGIA gain.
- **o**: the **offset** stored in the profile.
  - In the ARTIQ driver, this is described as “**IIR offset (negative setpoint)**”.
  - The controller operates on `(x + o)`.
  - If you want a setpoint `x_set`, you typically program `o = -x_set` so `(x + o) = x - x_set`, i.e. an error-like signal.
- **y**: the controller output state (also called the **integrator state**), an **unsigned amplitude scale factor**.
  - It is clipped to `[0, 1)` (i.e. 0…full scale).
  - Gateware uses `y` to continuously set the DDS amplitude (ASF scaling).

So, mentally:
- `x` is “what I measure”
- `x_set` is “what I want”
- `o = -x_set`
- `y` is “how hard I drive the AOM (DDS amplitude scale)”

---

## 2) FIR vs IIR: what’s the difference?

### FIR (finite impulse response)
An FIR filter is a **weighted sum of recent inputs** only:

\[
y[n] = \sum_{k=0}^{N-1} b_k\,x[n-k]
\]

No previous `y` values appear on the right-hand side → **no recursion**.

### IIR (infinite impulse response)
An IIR filter includes **feedback from previous outputs**:

\[
y[n] = \sum_{k=0}^{M} b_k\,x[n-k] + \sum_{k=1}^{P} a_k\,y[n-k]
\]

Past `y` values appear → **recursive**, and the impulse response can be “infinite”.

### What SU-Servo implements
SU-Servo’s “PI controller” is implemented as a **first-order IIR** with a **2-tap FIR-ish** feedforward part. The driver documents:

\[
a_0 y_n = a_1 y_{n-1} + b_0 \frac{(x_n + o)}{2} + b_1 \frac{(x_{n-1} + o)}{2}
\]

- The term `a1*y[n-1]` is the **IIR** (feedback) part.
- The `b0*(x[n]+o)` and `b1*(x[n-1]+o)` is a **2-tap FIR-like** part (it uses only a finite number of `x` samples).

**Important:** This “IIR feedback” is purely mathematical recursion inside the controller.  
**Control-systems feedback** (the physics loop) happens only if your measured `x` depends on your actuator output `y` (see next section).

---

## 3) “Where is the feedback?” (the confusion that trips everyone)

There are two “feedbacks” people mean:

### A) Filter recursion (“IIR feedback”)
If `a1 ≠ 0`, the controller’s difference equation feeds `y[n-1]` into `y[n]`. That’s IIR.

### B) Physical closed-loop feedback (“control feedback”)
You have **real closed-loop control** only if:

- DDS amplitude (`y`) drives an actuator (AOM RF power),
- that actuator changes the physical signal (optical power),
- a sensor (photodiode) produces a voltage,
- Sampler measures that voltage (`x`),
- and SU-Servo uses `x` to update `y`.

That physical loop is:

`y → AOM/beam → photodiode voltage x → SU-Servo DSP → y`

If your Sampler input comes from something **independent** of the DDS (e.g. a function generator), then SU-Servo is just doing **open-loop modulation**: “ADC controls DDS amplitude”.

---

## 4) What “P-only”, “I-only”, and “PI” mean in SU-Servo

The high-level API `Channel.set_iir(profile, adc, kp, ki, g, delay)` computes integer coefficients:

- `kp` is **dimensionless**: (output full-scale)/(input full-scale)
- `ki` is in **rad/s**
- `g` is a gain limit (dimensionless); `g=0` means “no limit”

### P-only
Set `ki = 0` → the driver programs `a1 = 0`, `b1 = 0`, `b0 ≈ kp * (normalization)`.

Conceptually: `y[n]` becomes (roughly) proportional to `(x[n] + o)` each cycle, clipped to `[0, 1)`.

### I-only (integrator)
Set `kp = 0`, `ki ≠ 0` → the output accumulates error over time.

### PI
Set both `kp` and `ki` (usually same sign). With a finite `g`, the “integrator” becomes leaky/limited.

---

## 5) Timing and practical limitations you must design around

### Update cadence
In SU-Servo mode, the FPGA updates each DDS channel periodically (e.g. around **~1.168 µs** per cycle in common configs).

That sets a *hard lower bound* on the loop delay, and usually makes achievable bandwidths **tens of kHz** (depending on AOM, photodiode, filters, etc.).

### “RAM mode” and “normal AD9910 driver features”
When the Urukul is in SU-Servo mode, you typically do **not** have the normal AD9910 “RAM mode” features available for that card: SU-Servo owns the full DDS update stream.

### Reading ADC / state while servo is running
The SU-Servo driver warns that reading state (ADC samples or `y`) can collide with gateware writes, producing invalid data.

**Most robust approach:** disable the servo (`set_config(enable=0)`), wait a few microseconds for the pipeline to drain, then read.

### Ramping setpoints
You can change the setpoint deterministically by calling:
- `Channel.set_dds_offset(profile, offset)` (offset is **negative setpoint** in full-scale units)

But if you attempt to update **every servo cycle** for long ramps, you may hit sustained RTIO event-rate limits. Practical ramps often:
- update at 1–50 kHz, or
- use coarser steps, or
- implement a gateware playback/ramp generator.

---

## 6) Three minimal example patterns (what you asked for)

You’ll find a companion `suservo_examples.py` with:
1) **Open-loop amplitude modulation**: ADC → DDS amplitude (no physical feedback required).
2) **Closed-loop constant intensity lock**: AOM + photodiode + Sampler form the plant.
3) **Closed-loop ramping setpoint**: update the setpoint over time by stepping `set_dds_offset()`.

All examples:
- initialize SU-Servo,
- program one profile,
- enable RF + IIR,
- enable/disable global servo,
- and do safe readback by disabling before reads.

---

## 7) Quick checklist for bring-up

- Confirm Urukul is AD9910 (4410) and DIP switch is set for SU-Servo mode (whole card).
- Pick a photodiode + transimpedance bandwidth comfortably above desired loop bandwidth.
- Start with:
  - **P-only**, very small |kp|, verify sign,
  - then add **I** slowly (ki) and consider a modest gain limit `g` to avoid wind-up.
- Use attenuation (Urukul step attenuator) to place the operating point safely; use SU-Servo to do the fast small corrections.
- For debugging readings: stop servo, then read `get_adc()` and `get_y()`.

---

If you want, I can also add a fourth example showing “between-shots stabilize one beam at a time, then run multi-beam with en_iir=0”, which is the pattern described in your email chain for shared photodiodes/fibres.
