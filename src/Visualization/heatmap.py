import os
from pathlib import Path
from typing import Union, Optional
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def plot_placement_heatmap(
    data: Union[str, Path, pd.DataFrame],
    x_col: str = "x_um",
    y_col: str = "y_um",
    bins: int = 500,
    cmap: str = "viridis",
    figsize: tuple = (10, 8),
    output_path: Optional[Union[str, Path]] = None,
    title: Optional[str] = None,
) -> None:
    """Generate a placement density heatmap from a CSV file or DataFrame.
    
    Args:
        data: Either a path to a CSV file (str or Path) or a pandas DataFrame
        x_col: Column name for x coordinates (default: "x_um")
        y_col: Column name for y coordinates (default: "y_um")
        bins: Number of bins for 2D histogram (default: 80)
        cmap: Colormap name (default: "viridis")
        figsize: Figure size tuple (default: (10, 8))
        output_path: Path to save the heatmap image (if None, displays instead)
        title: Optional title for the plot (if None, auto-generates from data source)
    """
    # Load data from CSV if path provided, otherwise use DataFrame directly
    if isinstance(data, (str, Path)):
        csv_path = Path(data)
        if not csv_path.exists():
            print(f"[WARNING] Cannot create heatmap: CSV file not found: {csv_path}")
            return
        df = pd.read_csv(csv_path)
        data_source_name = csv_path.stem
    elif isinstance(data, pd.DataFrame):
        df = data
        data_source_name = "placement"
    else:
        print(f"[WARNING] Cannot create heatmap: invalid data type: {type(data)}")
        return
    
    # Validate required columns exist
    if x_col not in df.columns or y_col not in df.columns:
        print(f"[WARNING] Cannot create heatmap: missing columns {x_col} or {y_col}")
        return
    
    x = df[x_col].to_numpy()
    y = df[y_col].to_numpy()
    
    if len(x) == 0:
        print("[WARNING] Cannot create heatmap: no placement data")
        return
    
    # 2D histogram over the placement area
    H, x_edges, y_edges = np.histogram2d(x, y, bins=bins)
    
    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(
        H.T,
        origin="lower",
        extent=[x_edges[0], x_edges[-1], y_edges[0], y_edges[-1]],
        aspect="equal",
        cmap=cmap,
        interpolation='nearest',
    )
    
    ax.set_xlabel(f"{x_col} (μm)", fontsize=12)
    ax.set_ylabel(f"{y_col} (μm)", fontsize=12)
    
    # Generate title if not provided
    if title is None:
        title = f"Placement Density Heatmap - {data_source_name} ({len(df)} cells)"
    ax.set_title(title, fontsize=14, fontweight='bold')
    
    cbar = fig.colorbar(im, ax=ax, label="Cell count per bin")
    cbar.ax.tick_params(labelsize=10)
    
    # Add grid for better readability
    ax.grid(True, alpha=0.3, linestyle='--', linewidth=0.5)
    
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=200, bbox_inches="tight")
        plt.close(fig)
        print(f"[DEBUG] Saved placement heatmap to: {output_path}")
    else:
        plt.show()


if __name__ == "__main__":
    csv_files = [
        "build/6502/6502_greedy_sa_placement.csv",
        "build/6502/6502_ppo_refined_placement.csv",
    ]

    for csv in csv_files:
        out = os.path.splitext(csv)[0] + "_heatmap.png"
        plot_placement_heatmap(csv, bins=80, output_path=out)
        print(f"Saved {out}")
