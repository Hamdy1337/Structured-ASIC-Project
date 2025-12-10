"""
cts_plotter.py: Visualization tools for Clock Tree Synthesis and placement.

This version generates an **interactive HTML** visualization of the CTS tree
using Plotly, and is designed to be drop-in compatible with the original
Matplotlib-based script that `test_cts.py` calls.

Usage (as called from test_cts.py):

    python src/visualization/cts_plotter.py cts \
        --placement <placement.csv> \
        --cts_data <cts.json> \
        --fabric_cells <fabric_cells.yaml> \
        --output <output.html> \
        --design <design_name>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Tuple

import pandas as pd
import plotly.graph_objects as go  # type: ignore[reportMissingTypeStubs]

# Add project root to path so "src.*" imports work when run as a script
sys.path.append(str(Path(__file__).parent.parent.parent))

# Kept for signature compatibility; currently not used inside this file,
# but other tooling may rely on the import side-effect.
try:
    from src.parsers.fabric_cells_parser import parse_fabric_cells_file  # noqa: F401
except Exception:
    # If the parser is not available, we still allow visualization to run.
    parse_fabric_cells_file = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _load_placement_xy(placement_csv: str) -> Tuple[pd.Series, pd.Series]:
    """Load placement CSV and return (x, y) series for logic cells.

    Supports column names: (x, y), (X, Y), or (x_um, y_um).
    """
    df = pd.read_csv(placement_csv)

    if {"x", "y"}.issubset(df.columns):
        return df["x"], df["y"]
    if {"X", "Y"}.issubset(df.columns):
        return df["X"], df["Y"]
    if {"x_um", "y_um"}.issubset(df.columns):
        return df["x_um"], df["y_um"]

    raise ValueError(
        "Placement CSV must contain 'x'/'y', 'X'/'Y', or 'x_um'/'y_um' "
        f"columns; got columns: {list(df.columns)}"
    )



# ---------------------------------------------------------------------------
# Plotly-based CTS visualization
# ---------------------------------------------------------------------------

def plot_cts_tree_interactive(
    placement_csv: str,
    fabric_cells_yaml: str,
    cts_json: str,
    output_path: str,
    design_name: str = "design",
) -> None:
    """Plot the CTS tree as an interactive Plotly HTML file.

    Parameters mirror the original ``plot_cts_tree`` so that this function
    can be used as a drop-in replacement from existing callers.
    ``fabric_cells_yaml`` is accepted for compatibility but is not used here.
    """

    # Load placement (logic cells)
    x_logic, y_logic = _load_placement_xy(placement_csv)

    # Load CTS JSON (sinks, buffers, connections)
    with open(cts_json) as f:
        cts_data = json.load(f)

    sinks = cts_data.get("sinks", [])
    buffers = cts_data.get("buffers", [])
    connections = cts_data.get("connections", [])

    xs_sinks = [s["x"] for s in sinks]
    ys_sinks = [s["y"] for s in sinks]
    names_sinks = [s.get("name", "") for s in sinks]

    xb = [b["x"] for b in buffers]
    yb = [b["y"] for b in buffers]
    levels = [b.get("level", 0) for b in buffers]
    names_bufs = [b.get("name", "") for b in buffers]

    fig = go.Figure()

    # Logic cells as faint background
    fig.add_trace(
        go.Scatter(
            x=x_logic,
            y=y_logic,
            mode="markers",
            marker=dict(size=3, opacity=0.25),
            name="Logic Cells",
            hoverinfo="skip",
        )
    )

    # Sinks (DFFs)
    fig.add_trace(
        go.Scatter(
            x=xs_sinks,
            y=ys_sinks,
            mode="markers",
            marker=dict(size=7, symbol="square"),
            name="Sinks (DFFs)",
            text=names_sinks,
            hovertemplate="Sink: %{text}<br>x=%{x:.1f}, y=%{y:.1f}<extra></extra>",
        )
    )

    # CTS buffers
    fig.add_trace(
        go.Scatter(
            x=xb,
            y=yb,
            mode="markers",
            marker=dict(size=9, symbol="triangle-up"),
            name="CTS Buffers",
            text=[f"{n}<br>level={lvl}" for n, lvl in zip(names_bufs, levels)],
            hovertemplate="%{text}<br>x=%{x:.1f}, y=%{y:.1f}<extra></extra>",
        )
    )

    # CTS connections (tree edges)
    for conn in connections:
        fx = conn["from"]["x"]
        fy = conn["from"]["y"]
        tx = conn["to"]["x"]
        ty = conn["to"]["y"]
        fig.add_trace(
            go.Scatter(
                x=[fx, tx],
                y=[fy, ty],
                mode="lines",
                line=dict(width=1),
                showlegend=False,
                hoverinfo="skip",
            )
        )

    fig.update_layout(
        title=f"CTS Tree Structure – {design_name}",
        xaxis_title="X (µm)",
        yaxis_title="Y (µm)",
        yaxis=dict(scaleanchor="x", scaleratio=1),
        template="plotly_white",
        width=1000,
        height=1000,
    )

    # Auto-zoom to the data region (logic + sinks + buffers)
    all_x = list(x_logic) + xs_sinks + xb
    all_y = list(y_logic) + ys_sinks + yb
    if all_x and all_y:
        span_x = max(all_x) - min(all_x)
        span_y = max(all_y) - min(all_y)
        pad_x = span_x * 0.05 if span_x > 0 else 10
        pad_y = span_y * 0.05 if span_y > 0 else 10
        fig.update_xaxes(range=[min(all_x) - pad_x, max(all_x) + pad_x])
        fig.update_yaxes(range=[min(all_y) - pad_y, max(all_y) + pad_y])

    # Ensure HTML suffix
    out = Path(output_path)
    if out.suffix.lower() != ".html":
        out = out.with_suffix(".html")

    out.parent.mkdir(parents=True, exist_ok=True)

    # Write to HTML
    fig.write_html(
        str(out),
        include_plotlyjs="cdn",
        full_html=True,
        auto_open=False,
    )
    print(f"[CTS] Interactive HTML written to {out}")
    
    # Write to PNG (requires kaleido)
    png_out = out.with_suffix(".png")
    try:
        fig.write_image(str(png_out), width=1600, height=1200)
        print(f"[CTS] Static PNG written to {png_out}")
    except Exception as e:
        print(f"[CTS] Warning: Could not write PNG. Error: {e}")



# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CTS visualization tool (interactive Plotly HTML)."
    )
    subparsers = parser.add_subparsers(dest="command")

    # CTS subcommand: mirror the interface used by test_cts.py
    p_cts = subparsers.add_parser("cts", help="Plot CTS tree for a design")
    p_cts.add_argument("--placement", required=True, help="Placement CSV file")
    p_cts.add_argument("--cts_data", required=True, help="CTS JSON file")
    p_cts.add_argument(
        "--fabric_cells",
        required=True,
        help="Fabric cells YAML (accepted for compatibility; currently unused)",
    )
    p_cts.add_argument(
        "--output",
        required=True,
        help="Output HTML path ('.html' will be appended if missing)",
    )
    p_cts.add_argument("--design", required=True, help="Design name for title")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "cts":
        plot_cts_tree_interactive(
            placement_csv=args.placement,
            fabric_cells_yaml=args.fabric_cells,
            cts_json=args.cts_data,
            output_path=args.output,
            design_name=args.design,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
