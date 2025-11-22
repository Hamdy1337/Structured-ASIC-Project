from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ==== EDIT THESE TWO LINES ====
CSV_PATH = "/Users/hamdy47/Downloads/6502_placement.csv"
DESIGN_NAME = "6502"                     


def main() -> None:
    csv_path = Path(CSV_PATH)
    df = pd.read_csv(csv_path)

    required_cols = {"x_um", "y_um"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    x = df["x_um"].astype(float).to_numpy()
    y = df["y_um"].astype(float).to_numpy()

    if x.size == 0:
        raise ValueError("CSV appears empty (no placement rows).")

    # ---- HPWL-like metric per cell ----
    # Use Manhattan distance from the design centroid as a proxy:
    #   length_i = |x_i - mean_x| + |y_i - mean_y|
    mean_x = float(x.mean())
    mean_y = float(y.mean())
    hpwl_like = np.abs(x - mean_x) + np.abs(y - mean_y)

    # ---- stats ----
    total_items = hpwl_like.size
    mean = float(hpwl_like.mean())
    median = float(np.median(hpwl_like))
    v_min = float(hpwl_like.min())
    v_max = float(hpwl_like.max())
    total_sum = float(hpwl_like.sum())

    # ---- plotting ----
    fig, ax = plt.subplots(figsize=(10, 5))
    counts, bins, patches = ax.hist(hpwl_like, bins=50)

    # color bars similar to the example
    cmap = plt.get_cmap("viridis")
    n = max(len(patches) - 1, 1)
    for i, p in enumerate(patches):
        p.set_facecolor(cmap(i / n))

    ax.set_xlabel("HPWL-like Length (um)")
    ax.set_ylabel("Number of Cells")
    ax.set_title(f"Net Length Distribution - {DESIGN_NAME}")

    stats_text = (
        f"Total items: {total_items:,}\n"
        f"Mean: {mean:.2f} um\n"
        f"Median: {median:.2f} um\n"
        f"Min: {v_min:.2f} um\n"
        f"Max: {v_max:.2f} um\n"
        f"Total: {total_sum:,.2f} um"
    )

    ax.text(
        0.98,
        0.98,
        stats_text,
        transform=ax.transAxes,
        fontsize=9,
        va="top",
        ha="right",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
    )

    fig.tight_layout()

    out_path = csv_path.with_name(f"{DESIGN_NAME}_net_length.png")
    fig.savefig(out_path, dpi=300)
    plt.close(fig)

    print(f"Saved histogram to: {out_path}")


if __name__ == "__main__":
    main()
