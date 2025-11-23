"""sasics_visualisation
Visualize the Structured ASIC layout:
 - Draw die and core outlines (from pins.yaml)
 - Draw pins on edges, colored by metal layer
 - Draw standard cells per tile definition (width from width_sites, height = row height)

Run:
  python -m src.Visualization.sasics_visualisation
"""
import pandas as pd
import plotly.graph_objects as go # type: ignore[reportMissingTypeStubs]

from typing import Dict, Set, Any, List, Optional
from src.parsers.fabric_db import get_fabric_db, Fabric
from src.parsers.pins_parser import load_and_validate as load_pins_df, PinsMeta

fig : go.Figure = go.Figure()


def classify_cell(cell_type: str) -> str:
    s = cell_type.lower()
    if "nand" in s:
        return "logic_nand"
    if "or" in s:
        return "logic_or"
    if "inv" in s:
        return "inv"
    if "buf" in s:
        return "clock_buf"
    if "dfbbp" in s:
        return "flop"
    if "tap" in s:
        return "tap"
    if "decap" in s:
        return "decap"
    if "conb" in s:
        return "tie"
    if "fill" in s:
        return "fill"
    return "other"


CELL_TYPE_COLORS: Dict[str, str] = {
    "logic_nand": "#1f77b4",
    "logic_or": "#2ca02c",
    "inv": "#ff7f0e",
    "clock_buf": "#9467bd",
    "flop": "#d62728",
    "tap": "#8c564b",
    "decap": "#e377c2",
    "tie": "#7f7f7f",
    "fill": "#bcbd22",
    "other": "#17becf",
}

PIN_LAYER_COLORS: Dict[str, str] = {
    "met2 INPUT": "#D01818",  # red
    "met3 INPUT": "#941D1D",  # dark gray
    "met2 OUTPUT": "#1DA01D",  # green
    "met3 OUTPUT": "#1D9494",  # teal
}

def draw_cells(
    fabric_dict: Fabric,
    fabric_df: pd.DataFrame,
    pins_meta: PinsMeta,
    pins_df: pd.DataFrame,
    decimals: int = 3,
    fast_mode: Optional[bool] = None,
    fast_threshold: int = 8000,
) -> None:
    """Render the scene with faster batching and cleaner numbers.

    - Batches all rectangle shapes and sets them via a single layout update.
    - Uses itertuples() and rounds coordinates to reduce FP artifacts.
    - Avoids per-item prints that slow down rendering.
    """
    site_width_um: float = float(fabric_dict.fabric_info["site_dimensions_um"]["width"])    
    site_height_um: float = float(fabric_dict.fabric_info["site_dimensions_um"]["height"])    

    # Accumulate shapes and update once (significantly faster than add_shape in a loop)
    shapes: List[Dict[str, Any]] = []

    # Die outline as a shape
    shapes.append({
        "type": "rect",
        "x0": 0,
        "y0": 0,
        "x1": pins_meta.die.width_um,
        "y1": pins_meta.die.height_um,
        "line": dict(color="Black", width=3),
        "fillcolor": None,
    })
    # Legend entry for die outline (trace-only, shapes don't show in legends)
    fig.add_trace(go.Scatter(  # type: ignore[reportUnknownMemberType]
        x=[0, pins_meta.die.width_um, pins_meta.die.width_um, 0, 0],
        y=[0, 0, pins_meta.die.height_um, pins_meta.die.height_um, 0],
        mode="lines",
        line=dict(color="Black", width=3),
        name="Die Outline",
        visible="legendonly",
        showlegend=True,
    ))

    # Core outline as a shape
    shapes.append({
        "type": "rect",
        "x0": pins_meta.die.core_margin_um,
        "y0": pins_meta.die.core_margin_um,
        "x1": pins_meta.core.width_um + pins_meta.die.core_margin_um,
        "y1": pins_meta.core.height_um + pins_meta.die.core_margin_um,
        "line": dict(color="Blue", width=3),
        "fillcolor": None,
    })
    # Legend entry for core outline (trace-only)
    fig.add_trace(go.Scatter(  # type: ignore[reportUnknownMemberType]
        x=[
            pins_meta.die.core_margin_um,
            pins_meta.core.width_um + pins_meta.die.core_margin_um,
            pins_meta.core.width_um + pins_meta.die.core_margin_um,
            pins_meta.die.core_margin_um,
            pins_meta.die.core_margin_um,
        ],
        y=[
            pins_meta.die.core_margin_um,
            pins_meta.die.core_margin_um,
            pins_meta.core.height_um + pins_meta.die.core_margin_um,
            pins_meta.core.height_um + pins_meta.die.core_margin_um,
            pins_meta.die.core_margin_um,
        ],
        mode="lines",
        line=dict(color="Blue", width=3),
        name="Core Outline",
        visible="legendonly",
        showlegend=True,
    ))
    print("Die and Core prepared.")

    # Choose rendering path based on number of cells
    num_cells: int = int(fabric_df.shape[0])
    use_fast: bool = fast_mode if fast_mode is not None else (num_cells > fast_threshold)

    if use_fast:
        print(f"Fast mode enabled for {num_cells} cells (threshold {fast_threshold}). Rendering outlines via WebGL lines.")
        # Build per-class line strips with None separators for each rectangle
        # Allow None separators in coordinate lists; annotate as List[Any]
        class_coords: Dict[str, Dict[str, List[Any]]] = {}

        used_cell_classes: Set[str] = set()
        for row in fabric_df.itertuples(index=False):
            cell_name = str(getattr(row, "cell_name"))
            cell_class = classify_cell(cell_name)
            used_cell_classes.add(cell_class)

            x_raw = float(getattr(row, "cell_x"))
            y_raw = float(getattr(row, "cell_y"))
            width_sites = int(getattr(row, "width_sites"))

            x0 = round(x_raw, decimals)
            y0 = round(y_raw, decimals)
            x1 = round(x0 + width_sites * site_width_um, decimals)
            y1 = round(y0 + site_height_um, decimals)

            entry = class_coords.setdefault(cell_class, {"x": [], "y": []})
            entry["x"].extend([x0, x1, x1, x0, x0, None])  # None breaks the polygon for separate rectangles
            entry["y"].extend([y0, y0, y1, y1, y0, None])

        # Add one WebGL line trace per class
        for cls in sorted(used_cell_classes):
            coords = class_coords.get(cls)
            if not coords:
                continue
            fig.add_trace(  # type: ignore[reportUnknownMemberType]
                go.Scattergl(
                x=coords["x"],
                y=coords["y"],
                mode="lines",
                line=dict(color=CELL_TYPE_COLORS.get(cls, "#000000"), width=1),
                name=f"Cell: {cls}",
                showlegend=True,
                fill="toself",
                fillcolor=CELL_TYPE_COLORS.get(cls, "#000000"),
                opacity=0.6,
            ))
        print(f"Cell outlines added as WebGL lines for {len(used_cell_classes)} classes.")
        # Apply only die/core shapes
        fig.update_layout(shapes=shapes)  # type: ignore[reportUnknownMemberType]
    else:
        i: int = 0
        used_cell_classes: Set[str] = set()
        for row in fabric_df.itertuples(index=False):
            cell_name = str(getattr(row, "cell_name"))
            cell_class = classify_cell(cell_name)
            cell_color = CELL_TYPE_COLORS.get(cell_class, "#000000")
            used_cell_classes.add(cell_class)

            x_raw = float(getattr(row, "cell_x"))
            y_raw = float(getattr(row, "cell_y"))
            width_sites = int(getattr(row, "width_sites"))

            cell_x_um = round(x_raw, decimals)
            cell_y_um = round(y_raw, decimals)
            cell_width_um = round(width_sites * site_width_um, decimals)
            cell_height_um = round(site_height_um, decimals)

            shapes.append({
                "type": "rect",
                "x0": cell_x_um,
                "y0": cell_y_um,
                "x1": cell_x_um + cell_width_um,
                "y1": cell_y_um + cell_height_um,
                "line": dict(color="Black", width=1),
                "fillcolor": cell_color,
                "opacity": 0.7,
            })
            i += 1
            if i % 1000 == 0:
                print(f"Prepared {i} cell rectangles...")

        # Apply all shapes at once
        fig.update_layout(shapes=shapes)  # type: ignore[reportUnknownMemberType]
        print(f"Cells prepared: {i} total; classes: {len(used_cell_classes)}")

        # Legend entries for cell classes (shapes don't appear in legends)
        for cls in sorted(used_cell_classes):
            fig.add_trace(  # type: ignore[reportUnknownMemberType]
                go.Scatter(
                x=[0],
                y=[0],
                mode="markers",
                marker=dict(size=10, color=CELL_TYPE_COLORS.get(cls, "#000000")),
                name=f"Cell: {cls}",
                visible="legendonly",
                showlegend=True,
            ))

    #Draw Pins (grouped by Layer + Direction for clean legends)
    pins_df = pins_df.copy()
    pins_df["_category"] = pins_df["layer"].astype(str) + " " + pins_df["direction"].astype(str)
    # Prefer legend order based on PIN_LAYER_COLORS mapping keys if present
    categories_in_data = list(dict.fromkeys(pins_df["_category"].tolist()))
    ordered_categories = [c for c in PIN_LAYER_COLORS.keys() if c in categories_in_data]
    ordered_categories += [c for c in categories_in_data if c not in ordered_categories]

    for cat in ordered_categories:
        grp = pins_df[pins_df["_category"] == cat]
        fig.add_trace(  # type: ignore[reportUnknownMemberType]
            go.Scatter(
            x=grp["x_um"].tolist(),
            y=grp["y_um"].tolist(),
            mode="markers",
            marker=dict(
                size=5,
                color=PIN_LAYER_COLORS.get(cat, "#000000"),
                line=dict(width=0),
            ),
            name=cat,
            hovertext=grp["name"].tolist(),
            hovertemplate="Pin: %{hovertext}<br>Category: " + cat + "<br>(%{x:.2f}, %{y:.2f})<extra></extra>",
            showlegend=True,
        ))
        print(f"Pins drawn for category: {cat}")


if __name__ == "__main__":
    fabric_file_path = "inputs/Platform/fabric.yaml"
    fabric_cells_file_path = "inputs/Platform/fabric_cells.yaml"
    pins_file_path = "inputs/Platform/pins.yaml"

    fabric_dict, fabric_df = get_fabric_db(fabric_file_path, fabric_cells_file_path)
    pins_df, pins_meta = load_pins_df(pins_file_path)

    draw_cells(fabric_dict, fabric_df, pins_meta, pins_df)

    fig.update_layout(  # type: ignore[reportUnknownMemberType]
        title="Structured ASIC Layout Visualization",
        xaxis_title="Width (um)",
        yaxis_title="Height (um)",
        yaxis=dict(
            scaleanchor="x",
            scaleratio=1,
            showgrid=False,
            zeroline=False,
        ),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
        ),
        plot_bgcolor="white",
    )

    # Write to HTML and open in the default browser. Using CDN keeps the file lighter for large designs.
    fig.write_html(  # type: ignore[reportUnknownMemberType]
        "build/structured_asic_layout.html",
        auto_open=True,
        include_plotlyjs="cdn",
        full_html=True,
    )
    