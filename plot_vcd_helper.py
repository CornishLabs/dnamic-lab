import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from vcdvcd import VCDVCD

try:
    from aliases import aliases as _ALIASES
except ImportError:
    _ALIASES: Dict[str, str] = {}


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
    Parse `$timescale <N><unit> $end` from a VCD header.
    Returns: (seconds_per_tick, label_str)
    """
    header = []
    with open(vcd_path, "r", errors="ignore") as f:
        for line in f:
            header.append(line)
            if "$enddefinitions" in line:
                break
    txt = "".join(header)

    m = re.search(r"\$timescale\s+(\d+)\s*([a-zA-Z]+)\s*\$end", txt, flags=re.S)
    if not m:
        return 1.0, "ticks"

    n = int(m.group(1))
    unit = m.group(2)
    if unit not in _TIMESCALE_UNITS:
        return 1.0, f"{n}{unit}"

    return n * _TIMESCALE_UNITS[unit], f"{n}{unit}"


# ----------------------------
# Name alias helpers
# ----------------------------
def build_reverse_alias_map(alias_map: Mapping[str, str]) -> Dict[str, str]:
    """
    Convert friendly->hardware aliases into hardware->friendly.
    If multiple friendly names map to the same hardware name, keep the first.
    """
    reverse: Dict[str, str] = {}
    for friendly_name, hardware_name in alias_map.items():
        reverse.setdefault(hardware_name, friendly_name)
    return reverse


def format_signal_name(raw_name: str, alias_map: Optional[Mapping[str, str]] = None) -> str:
    """
    Rewrite dotted VCD signal names with friendly aliases where possible.
    Example: ttl16.state -> ttl_quad.state
    """
    if not alias_map:
        return raw_name

    return ".".join(alias_map.get(part, part) for part in raw_name.split("."))


def build_display_name_map(
    names: List[str], alias_map: Optional[Mapping[str, str]] = None
) -> Dict[str, str]:
    """
    Build raw->display labels, de-duplicating collisions by appending raw names.
    """
    mapped = [(name, format_signal_name(name, alias_map)) for name in names]
    counts = Counter(display for _, display in mapped)
    return {
        raw: (f"{display} ({raw})" if counts[display] > 1 else display)
        for raw, display in mapped
    }


def display_name(name: str, display_map: Optional[Mapping[str, str]] = None) -> str:
    if not display_map:
        return name
    return display_map.get(name, name)


# ----------------------------
# Value parsing / classification
# ----------------------------
def _is_scalar_digital_value(v: str) -> bool:
    return v.strip() in ("0", "1", "x", "X", "z", "Z")


def vcd_value_to_float(v: str) -> float:
    """
    Best-effort conversion of a VCD value string to a float.
    - scalar: '0'/'1' -> 0/1
    - vector bitstrings: '10' -> 2 (binary), if only 0/1
    - real: raw VCD uses r<real> (e.g. r1.23)
    - x/z -> NaN
    """
    s = v.strip()
    if s == "":
        return np.nan

    if s[0] in ("r", "R"):
        s = s[1:].strip()

    if s in ("x", "X", "z", "Z"):
        return np.nan

    if len(s) > 1 and all(c in "01" for c in s):
        return float(int(s, 2))

    try:
        return float(s)
    except ValueError:
        return np.nan


@dataclass(frozen=True)
class SignalTV:
    name: str
    t_sec: np.ndarray
    v_raw: List[str]


def load_vcd_signals(
    vcd_path: str,
    *,
    include: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
) -> Tuple[float, str, Dict[str, SignalTV]]:
    """
    Load a VCD and return name -> SignalTV.
    include/exclude are regex patterns applied to reference names.
    """
    sec_per_tick, ts_label = parse_timescale_seconds(vcd_path)

    vcd = VCDVCD(vcd_path, store_tvs=True)
    names = list(vcd.references_to_ids.keys())

    include_patterns = [re.compile(pattern) for pattern in include or []]
    exclude_patterns = [re.compile(pattern) for pattern in exclude or []]

    if include_patterns:
        names = [n for n in names if any(pattern.search(n) for pattern in include_patterns)]
    if exclude_patterns:
        names = [n for n in names if not any(pattern.search(n) for pattern in exclude_patterns)]

    out: Dict[str, SignalTV] = {}
    for name in names:
        tv = vcd[name].tv  # [(time_tick, value_str), ...]
        if not tv:
            continue

        t_ticks = np.array([tt for tt, _ in tv], dtype=np.int64)
        v_raw = [vv for _, vv in tv]
        out[name] = SignalTV(
            name=name,
            t_sec=t_ticks.astype(float) * sec_per_tick,
            v_raw=v_raw,
        )

    return sec_per_tick, ts_label, out


def split_digital_analogue(signals: Dict[str, SignalTV]) -> Tuple[List[str], List[str]]:
    """
    Split into:
      - digital: only scalar '0/1/x/z' values observed
      - analogue: everything else (reals, vectors, integers, etc.)
    """
    digital, analogue = [], []
    for name, signal in signals.items():
        if all(_is_scalar_digital_value(v) for v in signal.v_raw):
            digital.append(name)
        else:
            analogue.append(name)
    return digital, analogue


def get_end_time(signals: Mapping[str, SignalTV]) -> float:
    return max((float(signal.t_sec[-1]) for signal in signals.values() if signal.t_sec.size), default=0.0)


# ----------------------------
# Binning helpers (min/max for analogue; last-value for digital)
# ----------------------------
def make_time_bins(t_end: float, n_bins: int) -> np.ndarray:
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    if t_end <= 0:
        return np.array([0.0, 1.0])
    return np.linspace(0.0, t_end, int(n_bins) + 1)


def bin_last_value(t: np.ndarray, v: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """
    For each bin, return the last value at or before bin end.
    """
    ends = edges[1:]
    idx = np.searchsorted(t, ends, side="right") - 1
    out = np.full(len(ends), np.nan, dtype=float)
    good = idx >= 0
    out[good] = v[idx[good]]
    return out


def bin_min_max(t: np.ndarray, v: np.ndarray, edges: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    For each bin [edges[i], edges[i+1]), compute min/max over values in that span,
    including the value active at the bin start (piecewise-constant signals).
    """
    n = len(edges) - 1
    vmin = np.full(n, np.nan, dtype=float)
    vmax = np.full(n, np.nan, dtype=float)

    i0 = np.searchsorted(t, edges[:-1], side="left")
    i1 = np.searchsorted(t, edges[1:], side="left")

    for i in range(n):
        a, b = int(i0[i]), int(i1[i])
        candidates = []

        if a > 0:
            candidates.append(v[a - 1])
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
    display_map: Optional[Mapping[str, str]] = None,
):
    """
    Clean timing diagram style grid:
      y = signals, x = time, color = off/on/unknown
    """
    mats = []
    for name in names:
        signal = signals[name]
        v_num = np.array([vcd_value_to_float(v) for v in signal.v_raw], dtype=float)
        binned = bin_last_value(signal.t_sec, v_num, edges)

        state = np.full_like(binned, 2, dtype=int)  # unknown
        state[np.isfinite(binned) & (binned <= 0.5)] = 0
        state[np.isfinite(binned) & (binned > 0.5)] = 1
        mats.append(state)

    if not mats:
        raise ValueError("No digital signals to plot.")

    matrix = np.vstack(mats)
    y_edges = np.arange(len(names) + 1, dtype=float)

    cmap = ListedColormap(["darkred", "lime", "0.75"])
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5], cmap.N)

    fig, ax = plt.subplots(figsize=(18, max(6, 0.25 * len(names))), constrained_layout=True)
    ax.pcolormesh(
        edges,
        y_edges,
        matrix,
        cmap=cmap,
        norm=norm,
        shading="flat",
        edgecolors=(0, 0, 0, 0.06),
        linewidth=0.05,
        antialiased=False,
    )

    ax.set_yticks(np.arange(len(names)) + 0.5)
    ax.set_yticklabels([display_name(name, display_map) for name in names], fontsize=label_fontsize)
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
    display_map: Optional[Mapping[str, str]] = None,
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

    x_step = np.repeat(edges, 2)[1:-1]

    for i, name in enumerate(names):
        ax = axs[i]
        signal = signals[name]
        v = np.array([vcd_value_to_float(vv) for vv in signal.v_raw], dtype=float)

        vmin, vmax = bin_min_max(signal.t_sec, v, edges)
        vmin_s = np.repeat(vmin, 2)
        vmax_s = np.repeat(vmax, 2)

        ax.fill_between(x_step, vmin_s, vmax_s, alpha=0.25)

        if show_center_line:
            mid = 0.5 * (vmin + vmax)
            ax.plot(x_step, np.repeat(mid, 2), linewidth=1.0)

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
        ax.text(
            -0.01,
            0.5,
            display_name(name, display_map),
            transform=ax.transAxes,
            ha="right",
            va="center",
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
    vcd_path = "./results/2026-02-19/16/000001001-LoadMOTToTweezersImageTEMPExp/trace.vcd"
    alias_map = build_reverse_alias_map(_ALIASES)

    _, _, signals = load_vcd_signals(
        vcd_path,
        include=None,
        exclude=[r"\$"],
    )

    display_map = build_display_name_map(list(signals.keys()), alias_map)
    digital_names, analogue_names = split_digital_analogue(signals)
    digital_names.sort(key=lambda n: display_map[n])
    analogue_names.sort(key=lambda n: display_map[n])

    t_end = get_end_time(signals)
    n_bins = 3000
    edges = make_time_bins(t_end, n_bins)

    if digital_names:
        _, _ = plot_digital_grid(
            signals,
            digital_names,
            edges,
            label_fontsize=6,
            display_map=display_map,
        )
        plt.show()

    if analogue_names:
        _, _ = plot_analogue_minmax_stacked(
            signals,
            analogue_names,
            edges,
            label_fontsize=6,
            display_map=display_map,
        )
        plt.show()
