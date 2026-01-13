import re
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.colors import ListedColormap, BoundaryNorm

from vcdvcd import VCDVCD  # pip install vcdvcd


# ----------------------------
# Timescale parsing (seconds per VCD tick)
# ----------------------------
_TIMESCALE_UNITS = {
    "s": 1.0,
    "ms": 1e-3,
    "us": 1e-6,
    "ns": 1e-9,
    "ps": 1e-12,
    "fs": 1e-15,
}

def parse_timescale_seconds(vcd_path: str) -> Tuple[float, str]:
    """
    Parse `$timescale <N><unit> $end` from the VCD header.
    Returns: (seconds_per_tick, label_str)
    """
    header = []
    with open(vcd_path, "r", errors="ignore") as f:
        for line in f:
            header.append(line)
            if "$enddefinitions" in line:
                break
    txt = "".join(header)

    # handle both single-line and multi-line timescale declarations
    m = re.search(r"\$timescale\s+(\d+)\s*([a-zA-Z]+)\s*\$end", txt, flags=re.S)
    if not m:
        # if missing, fall back to "ticks" (still works if you treat x-axis as ticks)
        return 1.0, "ticks"

    n = int(m.group(1))
    unit = m.group(2)
    if unit not in _TIMESCALE_UNITS:
        return 1.0, f"{n}{unit}"

    return n * _TIMESCALE_UNITS[unit], f"{n}{unit}"


# ----------------------------
# Value parsing / classification
# ----------------------------
def _is_scalar_digital_value(v: str) -> bool:
    v = v.strip()
    return v in ("0", "1", "x", "X", "z", "Z")

def vcd_value_to_float(v: str) -> float:
    """
    Best-effort conversion of a VCD value string to a float.
    - scalar: '0'/'1' -> 0/1
    - vector bitstrings: '10' -> 2 (binary), if only 0/1
    - real: raw VCD uses r<real> (e.g. r1.23). Some parsers may strip 'r'.
    - x/z -> NaN
    """
    s = v.strip()
    if s == "":
        return np.nan

    # raw VCD real values are denoted by 'r' or 'R' prefix in the dump format :contentReference[oaicite:1]{index=1}
    if s[0] in ("r", "R"):
        s = s[1:].strip()

    if s in ("x", "X", "z", "Z"):
        return np.nan

    # if it's a pure bitstring (vector) like '10', treat as binary
    if len(s) > 1 and all(c in "01" for c in s):
        return float(int(s, 2))

    # plain numeric
    try:
        return float(s)
    except ValueError:
        return np.nan


@dataclass(frozen=True)
class SignalTV:
    name: str
    t_sec: np.ndarray     # change times in seconds
    v_raw: List[str]      # raw value strings (same length as t_sec)


def load_vcd_signals(
    vcd_path: str,
    *,
    include: Optional[List[str]] = None,   # regex patterns
    exclude: Optional[List[str]] = None,   # regex patterns
) -> Tuple[float, str, Dict[str, SignalTV]]:
    """
    Load a VCD and return dict of signals: name -> SignalTV
    include/exclude are regex patterns applied to the *reference name*.
    """
    sec_per_tick, ts_label = parse_timescale_seconds(vcd_path)

    vcd = VCDVCD(vcd_path, store_tvs=True)
    all_names = list(vcd.references_to_ids.keys())

    def _match_any(name: str, patterns: List[str]) -> bool:
        return any(re.search(p, name) for p in patterns)

    names = all_names
    if include:
        names = [n for n in names if _match_any(n, include)]
    if exclude:
        names = [n for n in names if not _match_any(n, exclude)]

    out: Dict[str, SignalTV] = {}
    for n in names:
        tv = vcd[n].tv  # [(time_tick, value_str), ...] :contentReference[oaicite:2]{index=2}
        if not tv:
            continue
        t_ticks = np.array([tt for (tt, _) in tv], dtype=np.int64)
        v_raw = [vv for (_, vv) in tv]
        out[n] = SignalTV(name=n, t_sec=t_ticks.astype(float) * sec_per_tick, v_raw=v_raw)

    return sec_per_tick, ts_label, out


def split_digital_analogue(signals: Dict[str, SignalTV]) -> Tuple[List[str], List[str]]:
    """
    Split into:
      - digital: only scalar '0/1/x/z' values observed
      - analogue: everything else (reals, vectors, integers, etc.)
    """
    digital, analogue = [], []
    for name, s in signals.items():
        if all(_is_scalar_digital_value(v) for v in s.v_raw):
            digital.append(name)
        else:
            analogue.append(name)
    return digital, analogue


# ----------------------------
# Binning helpers (min/max for analogue; last-value for digital)
# ----------------------------
def make_time_bins(t_end: float, n_bins: int) -> np.ndarray:
    if t_end <= 0:
        return np.array([0.0, 1.0])
    return np.linspace(0.0, t_end, int(n_bins) + 1)

def bin_last_value(t: np.ndarray, v: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """
    For each bin, return the last value at or before bin end.
    v should already be numeric (0/1/NaN etc.).
    """
    ends = edges[1:]
    idx = np.searchsorted(t, ends, side="right") - 1
    out = np.full(len(ends), np.nan, dtype=float)
    good = idx >= 0
    out[good] = v[idx[good]]
    return out

def bin_min_max(t: np.ndarray, v: np.ndarray, edges: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    For each bin [edges[i], edges[i+1]), compute min/max over values that occur in that time span,
    including the value active at the bin start (piecewise-constant signals).
    """
    n = len(edges) - 1
    vmin = np.full(n, np.nan, dtype=float)
    vmax = np.full(n, np.nan, dtype=float)

    # indices of first change in each bin (start), and first change after each bin (end)
    i0 = np.searchsorted(t, edges[:-1], side="left")
    i1 = np.searchsorted(t, edges[1:], side="left")

    for i in range(n):
        a, b = int(i0[i]), int(i1[i])
        candidates = []

        # value active at bin start = last value before edges[i]
        if a > 0:
            candidates.append(v[a - 1])

        # values that change within the bin
        if b > a:
            candidates.extend(v[a:b])

        if candidates:
            arr = np.array(candidates, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size:
                vmin[i] = float(np.min(arr))
                vmax[i] = float(np.max(arr))

    return vmin, vmax


# ----------------------------
# Plotting
# ----------------------------
def plot_digital_grid(
    signals: Dict[str, SignalTV],
    names: List[str],
    edges: np.ndarray,
    *,
    label_fontsize: int = 7,
):
    """
    Clean timing diagram style grid:
      y = signals, x = time, color = off/on/unknown
    """
    # build matrix: (n_signals, n_bins) in {0,1,2} where 2=unknown
    mats = []
    for n in names:
        s = signals[n]
        v_num = np.array([vcd_value_to_float(v) for v in s.v_raw], dtype=float)  # 0/1/NaN
        b = bin_last_value(s.t_sec, v_num, edges)
        state = np.full_like(b, 2, dtype=int)          # unknown
        state[np.isfinite(b) & (b <= 0.5)] = 0         # off
        state[np.isfinite(b) & (b > 0.5)] = 1          # on
        mats.append(state)

    if not mats:
        raise ValueError("No digital signals to plot.")

    M = np.vstack(mats)  # (signals, bins)

    x_edges = edges
    y_edges = np.arange(len(names) + 1, dtype=float)

    cmap = ListedColormap(["darkred", "lime", "0.75"])
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5], cmap.N)

    fig, ax = plt.subplots(figsize=(18, max(6, 0.25 * len(names))), constrained_layout=True)
    ax.pcolormesh(
        x_edges, y_edges, M,
        cmap=cmap, norm=norm,
        shading="flat",
        edgecolors=(0, 0, 0, 0.06),
        linewidth=0.05,
        antialiased=False,
    )

    ax.set_yticks(np.arange(len(names)) + 0.5)
    ax.set_yticklabels(names, fontsize=label_fontsize)
    ax.invert_yaxis()

    ax.set_xlabel("Time (s)")
    ax.xaxis.set_major_locator(mticker.MaxNLocator(12))
    ax.grid(False)

    return fig, ax


def plot_analogue_minmax_stacked(
    signals: Dict[str, SignalTV],
    names: List[str],
    edges: np.ndarray,
    *,
    label_fontsize: int = 7,
    show_center_line: bool = True,
):
    """
    Stacked axes: each channel shows a min-max envelope per time bin.
    """
    n = len(names)
    if n == 0:
        raise ValueError("No analogue signals to plot.")

    fig_h = max(6.0, 0.6 * n)
    fig, axs = plt.subplots(n, 1, sharex=True, figsize=(18, fig_h), constrained_layout=True)
    if n == 1:
        axs = [axs]

    # step-style x for envelopes
    x_step = np.repeat(edges, 2)[1:-1]  # length 2*n_bins

    for i, name in enumerate(names):
        ax = axs[i]
        s = signals[name]
        v = np.array([vcd_value_to_float(vv) for vv in s.v_raw], dtype=float)

        vmin, vmax = bin_min_max(s.t_sec, v, edges)
        # convert bins to step arrays
        vmin_s = np.repeat(vmin, 2)
        vmax_s = np.repeat(vmax, 2)

        ax.fill_between(x_step, vmin_s, vmax_s, alpha=0.25)

        if show_center_line:
            mid = 0.5 * (vmin + vmax)
            ax.plot(x_step, np.repeat(mid, 2), linewidth=1.0)

        # right-side min/max ticks (like your old plot style)
        finite = np.isfinite(vmin) & np.isfinite(vmax)
        if np.any(finite):
            lo = float(np.nanmin(vmin[finite]))
            hi = float(np.nanmax(vmax[finite]))
            if np.isclose(lo, hi):
                hi = lo + 1.0
            ax.set_yticks([lo, hi])
            ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.3g"))

        ax.yaxis.set_ticks_position("right")
        ax.tick_params(axis="y", left=False, right=True, labelsize=label_fontsize)

        # left label
        ax.text(
            -0.01, 0.5, name,
            transform=ax.transAxes,
            ha="right", va="center",
            fontsize=label_fontsize,
            clip_on=False,
        )

        ax.spines["left"].set_visible(False)

    axs[-1].set_xlabel("Time (s)")
    axs[-1].xaxis.set_major_locator(mticker.MaxNLocator(12))
    return fig, axs


# ----------------------------
# Example usage
# ----------------------------
if __name__ == "__main__":
    vcd_path = "./results/2026-01-12/18/000000878-MOTLoadExp/trace.vcd"

    # Use include/exclude to avoid plotting *everything*
    sec_per_tick, ts_label, sigs = load_vcd_signals(
        vcd_path,
        include=None,                    # e.g. [r"^top\.", r"adc", r"dac"]
        exclude=[r"\$"],                 # often skips internal "$" refs if any
    )

    digital_names, analogue_names = split_digital_analogue(sigs)

    # Determine end time from loaded signals
    t_end = 0.0
    for s in sigs.values():
        t_end = max(t_end, float(np.max(s.t_sec)))

    # Choose column count: controls “diagram” resolution (and min/max binning)
    n_bins = 3000
    edges = make_time_bins(t_end, n_bins)

    # Digital timing grid (no step-name blocks, no rescaling)
    if digital_names:
        fig, ax = plot_digital_grid(sigs, digital_names, edges, label_fontsize=6)
        plt.show()

    # Analogue min–max envelopes
    if analogue_names:
        fig, axs = plot_analogue_minmax_stacked(sigs, analogue_names, edges, label_fontsize=6)
        plt.show()
