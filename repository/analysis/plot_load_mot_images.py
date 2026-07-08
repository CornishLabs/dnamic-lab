#!/usr/bin/env python3
"""Plot saved LoadRbMOT images grouped by cool DDS amplitude.

Edit the configuration block below, then run this file directly, for example:

    /home/lab/artiq-files/install/ndscan/.venv/bin/python \
        repository/analysis/plot_load_mot_images.py
"""

from __future__ import annotations

import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
import numpy as np

from ndscan.results import read_scan_site_snapshot


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SNAPSHOT_PATH = Path(
    "./results/2026-07-06/16/000007232-LoadRbMOT.h5"
)

SITE_PATH = ()
IMAGE_CHANNEL = "mot_image"

# Use the experiment's diagnostic output by default, i.e. the value actually
# sent to the DDS setup code. Change to "cool_dds_amp" to use the scan parameter
# series instead.
COOL_AMP_SERIES = "cool_dds_amp_applied"

TOTAL_FLUORESCENCE_SERIES = "total_fluorescence"
CMAP = "magma"

# If many amplitudes were scanned, keep the image figure readable.
MAX_IMAGE_COLUMNS = 5
TARGET_GRID_ASPECT = 3

# Optional outputs. Leave as None to skip saving.
SAVE_AVERAGE_IMAGE_FIGURE = None
SAVE_FLUORESCENCE_FIGURE = None
SAVE_AVERAGE_IMAGE_STACK = None

LIST_AVAILABLE_SERIES = True


# ---------------------------------------------------------------------------
# Load saved series
# ---------------------------------------------------------------------------

snapshot = read_scan_site_snapshot(SNAPSHOT_PATH)
site = snapshot.get_site(SITE_PATH)

if LIST_AVAILABLE_SERIES:
    print(f"Available series on site {'/'.join(site.path) or '<root>'}:")
    print("-" * 80)
    for item in site.describe_series():
        print(f"{item.kind:14} {item.path:32} shape={item.shape} dtype={item.dtype}")
    print("-" * 80)

images = np.asarray(site.series(IMAGE_CHANNEL))
cool_amps = np.asarray(site.series(COOL_AMP_SERIES), dtype=float)

if images.ndim != 3:
    raise ValueError(
        f"{IMAGE_CHANNEL!r} has shape {images.shape}; expected (points, y, x)"
    )
if len(images) != len(cool_amps):
    raise ValueError(
        f"{IMAGE_CHANNEL!r} has {len(images)} points but {COOL_AMP_SERIES!r} has "
        f"{len(cool_amps)}"
    )

total_fluorescence = None
if TOTAL_FLUORESCENCE_SERIES in site.available_series_paths():
    total_fluorescence = np.asarray(
        site.series(TOTAL_FLUORESCENCE_SERIES),
        dtype=float,
    )

print(f"Loaded image stack: shape={images.shape}, dtype={images.dtype}")
print(f"Grouping by {COOL_AMP_SERIES!r}")


# ---------------------------------------------------------------------------
# Average images for each unique cool DDS amplitude
# ---------------------------------------------------------------------------

unique_amps = np.unique(cool_amps)
average_images = []
counts_per_amp = []
mean_fluorescence = []
std_fluorescence = []

for amp in unique_amps:
    mask = cool_amps == amp
    grouped_images = images[mask]
    average_images.append(np.mean(grouped_images, axis=0))
    counts_per_amp.append(int(np.count_nonzero(mask)))

    if total_fluorescence is not None:
        grouped_fluorescence = total_fluorescence[mask]
        mean_fluorescence.append(float(np.mean(grouped_fluorescence)))
        std_fluorescence.append(float(np.std(grouped_fluorescence)))

average_images = np.asarray(average_images)

print("Grouped images:")
for amp, count in zip(unique_amps, counts_per_amp, strict=True):
    print(f"  cool DDS amp {amp:.8g}: {count} image(s)")

if SAVE_AVERAGE_IMAGE_STACK is not None:
    save_path = Path(SAVE_AVERAGE_IMAGE_STACK)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(save_path, average_images)
    print(f"Saved average image stack to {save_path}")


# ---------------------------------------------------------------------------
# Plot one average MOT image per unique cool DDS amplitude
# ---------------------------------------------------------------------------

num_amps = len(unique_amps)
num_columns = min(
    MAX_IMAGE_COLUMNS,
    num_amps,
    max(1, math.ceil(math.sqrt(num_amps * TARGET_GRID_ASPECT))),
)
num_rows = math.ceil(num_amps / num_columns)

image_figure, axes = plt.subplots(
    num_rows,
    num_columns,
    figsize=(4 * num_columns, 4 * num_rows),
    sharex=True,
    sharey=True,
    squeeze=False,
    gridspec_kw={"wspace": 0.03, "hspace": 0.12},
)

vmin = float(np.nanmin(average_images))
vmax = float(np.nanmax(average_images))

for amp_index, amp in enumerate(unique_amps):
    row = amp_index // num_columns
    column = amp_index % num_columns
    axis = axes[row][column]
    image_plot = axis.imshow(
        average_images[amp_index],
        cmap=CMAP,
        origin="upper",
        vmin=vmin,
        vmax=vmax,
    )
    axis.set_title(f"{amp:.4f}", fontsize=10, pad=2)
    axis.tick_params(labelbottom=False, labelleft=False, length=0)

for empty_index in range(num_amps, num_rows * num_columns):
    row = empty_index // num_columns
    column = empty_index % num_columns
    axes[row][column].axis("off")

image_figure.colorbar(
    image_plot,
    ax=axes.ravel().tolist(),
    shrink=0.9,
    fraction=0.035,
    pad=0.015,
    label="counts",
)
image_figure.suptitle("Average MOT image by cool DDS amplitude", y=0.98)

if SAVE_AVERAGE_IMAGE_FIGURE is not None:
    save_path = Path(SAVE_AVERAGE_IMAGE_FIGURE)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    image_figure.savefig(save_path, dpi=150)
    print(f"Saved average image figure to {save_path}")


# ---------------------------------------------------------------------------
# Scatter plot total fluorescence against cool DDS amplitude
# ---------------------------------------------------------------------------

if total_fluorescence is not None:
    fluorescence_figure, axis = plt.subplots()

    axis.scatter(
        cool_amps,
        total_fluorescence,
        color="0.25",
        alpha=0.65,
        label="shots",
    )

    if mean_fluorescence:
        axis.errorbar(
            unique_amps,
            np.asarray(mean_fluorescence),
            yerr=np.asarray(std_fluorescence),
            fmt="o",
            color="tab:red",
            capsize=3,
            label="mean +/- std",
        )

    axis.set_xlabel(COOL_AMP_SERIES)
    axis.set_ylabel("total fluorescence / counts")
    axis.set_title("MOT fluorescence vs cool DDS amplitude")
    axis.legend()
    fluorescence_figure.tight_layout()

    if SAVE_FLUORESCENCE_FIGURE is not None:
        save_path = Path(SAVE_FLUORESCENCE_FIGURE)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fluorescence_figure.savefig(save_path, dpi=150)
        print(f"Saved fluorescence figure to {save_path}")
else:
    print(f"No {TOTAL_FLUORESCENCE_SERIES!r} series found; skipping fluorescence plot.")

plt.show()
