import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def plot_placement_heatmap(
    csv_path,
    x_col="x_um",
    y_col="y_um",
    bins=80,
    cmap="viridis",
    figsize=(8, 6),
    output_path=None,
):

    df = pd.read_csv(csv_path)

    x = df[x_col].to_numpy()
    y = df[y_col].to_numpy()

    # 2D histogram over the placement area
    H, x_edges, y_edges = np.histogram2d(x, y, bins=bins)

    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(
        H.T,
        origin="lower",
        extent=[x_edges[0], x_edges[-1], y_edges[0], y_edges[-1]],
        aspect="equal",
        cmap=cmap,
    )

    ax.set_xlabel(x_col)
    ax.set_ylabel(y_col)
    ax.set_title(f"Placement Heatmap - {os.path.basename(csv_path)}")
    cbar = fig.colorbar(im, ax=ax, label="Cell count")

    if output_path is not None:
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
        plt.close(fig)
    else:
        plt.show()


csv_files = [
    "/Users/hamdy47/Downloads/ppo_fullplacer_only.csv",
    "/Users/hamdy47/Downloads/ppo_refined_placement_smoke.csv",
    "/Users/hamdy47/Downloads/ppo_refined_placement.csv",
]

for csv in csv_files:
    out = os.path.splitext(csv)[0] + "_heatmap.png"
    plot_placement_heatmap(csv, bins=80, output_path=out)
    print(f"Saved {out}")
