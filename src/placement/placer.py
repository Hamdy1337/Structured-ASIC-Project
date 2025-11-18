"""
placer.py: Main module for assigning cells on the sASIC fabric.
    python -m src.placement.placer
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Any, Optional
from pathlib import Path
import time
import pandas as pd

from src.parsers.fabric_db import get_fabric_db, Fabric
from src.parsers.pins_parser import load_and_validate as load_pins_df
from src.parsers.netlist_parser import get_netlist_graph

from src.placement.port_assigner import assign_ports_to_pins
from src.placement.dependency_levels import build_dependency_levels
from src.placement.placement_utils import (
    build_sites,
    fixed_points_from_pins,
    nets_by_cell,
    in_out_nets_by_cell,
    median,
    nearest_site,
    hpwl_for_nets,
    driver_points,
)
from src.placement.simulated_annealing import anneal_batch


def place_cells_greedy_sim_anneal(
    fabric: Fabric,
    fabric_df: pd.DataFrame,
    pins_df: pd.DataFrame,
    ports_df: pd.DataFrame,
    netlist_graph: pd.DataFrame,
    sa_moves_per_temp: int = 200,
    sa_cooling_rate: float = 0.90,
    sa_T_initial: Optional[float] = None,
    sa_p_refine: float = 0.7,
    sa_p_explore: float = 0.3,
    sa_refine_max_distance: float = 100.0,
    sa_W_initial: float = 0.5,
    sa_seed: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Place cells on the fabric using a greedy simulated annealing algorithm.

    Steps:
    1) Assign ports to pins (done above).
    2) Levelize cells from netlist.
    3) Build available site list from fabric.
    4) Greedy initial placement (median-of-drivers target, Manhattan nearest, L2 tie-breaker).
    5) Small-batch simulated annealing per level to reduce local HPWL.

    Args:
        fabric: Fabric dataclass
        fabric_df: DataFrame with fabric cell information
        pins_df: DataFrame with pin information
        ports_df: DataFrame with port information
        netlist_graph: DataFrame with netlist connectivity
        sa_moves_per_temp: Number of moves attempted at each temperature step (default: 200)
        sa_cooling_rate: Cooling rate alpha (default: 0.90). Higher = slower cooling
        sa_T_initial: Initial temperature. If None, auto-calculates from initial HPWL (default: None)
        sa_p_refine: Probability of refine move (default: 0.7). Should sum with sa_p_explore to 1.0
        sa_p_explore: Probability of explore move (default: 0.3). Should sum with sa_p_refine to 1.0
        sa_refine_max_distance: Maximum Manhattan distance for refine moves in microns (default: 100.0)
        sa_W_initial: Initial exploration window size as fraction of die size (default: 0.5 = 50%)
        sa_seed: Random seed for reproducibility (default: 42)

    Returns:
        (updated_pins_df, placement_df)
    """
    updated_pins, _assign = assign_ports_to_pins(pins_df, ports_df)

    # Build inputs for placement
    sites_df = build_sites(fabric_df)
    free_site_ids: List[int] = sites_df["site_id"].tolist()
    fixed_pts = fixed_points_from_pins(updated_pins)
    g_levels = build_dependency_levels(updated_pins, netlist_graph)
    ins_by_cell, outs_by_cell = in_out_nets_by_cell(g_levels)
    cell_to_nets = nets_by_cell(g_levels)

    # Order cells by level
    cell_levels = g_levels[["cell_name", "dependency_level"]].drop_duplicates()
    order = cell_levels.sort_values(by=["dependency_level", "cell_name"]).reset_index(drop=True)

    # Placement state
    assignments: Dict[str, int] = {}  # cell_name -> site_id
    pos_cells: Dict[str, Tuple[float, float]] = {}

    # Timing: Track annealing time
    total_annealing_time = 0.0
    greedy_start_time = time.time()

    # Level-by-level greedy + SA in small batches
    batch_size = 24
    for lvl in sorted(order["dependency_level"].unique()):
        # Build list of cell names (already strings or convertible)
        cells_series: pd.Series = order.loc[order["dependency_level"] == lvl, "cell_name"]  # type: ignore[assignment]
        level_cells = [str(x) for x in cells_series.tolist()]
        
        # Greedy initial placement for level
        for c in level_cells:
            # Compute target (median of driver points)
            pts = driver_points(c, ins_by_cell, outs_by_cell, pos_cells, fixed_pts)
            if pts:
                tx = median([p[0] for p in pts])
                ty = median([p[1] for p in pts])
            else:
                # fallback: center of available sites
                tx = float(sites_df["x_um"].median())
                ty = float(sites_df["y_um"].median())
            
            sid = nearest_site((tx, ty), free_site_ids, sites_df)
            if sid is None:
                continue
            
            assignments[c] = sid
            pos_cells[c] = (float(sites_df.at[sid, "x_um"]), float(sites_df.at[sid, "y_um"]))  # type: ignore[arg-type]
            # consume site
            free_site_ids.remove(sid)
        
        # Small-batch SA within the level
        for i in range(0, len(level_cells), batch_size):
            batch: List[str] = [c for c in level_cells[i:i + batch_size] if c in assignments]
            if len(batch) < 2:
                continue  # Skip batches with < 2 cells
            
            # Time this annealing batch
            anneal_start = time.time()
            anneal_batch(
                batch, pos_cells, assignments, sites_df, cell_to_nets, fixed_pts,
                iters=sa_moves_per_temp,
                alpha=sa_cooling_rate,
                T_initial=sa_T_initial,
                p_refine=sa_p_refine,
                p_explore=sa_p_explore,
                refine_max_distance=sa_refine_max_distance,
                W_initial=sa_W_initial,
                seed=sa_seed
            )
            anneal_end = time.time()
            total_annealing_time += (anneal_end - anneal_start)
    
    greedy_end_time = time.time()
    total_placement_time = greedy_end_time - greedy_start_time
    greedy_time = total_placement_time - total_annealing_time
    
    # Debug: Print timing information
    print(f"\n[DEBUG] Placement Timing:")
    print(f"  Greedy placement time: {greedy_time:.3f} seconds")
    print(f"  Total annealing time: {total_annealing_time:.3f} seconds")
    print(f"  Total placement time: {total_placement_time:.3f} seconds")
    if total_placement_time > 0:
        print(f"  Annealing percentage: {(total_annealing_time / total_placement_time * 100):.1f}%")

    # Build placement DataFrame
    placement_rows: List[Dict[str, Any]] = []
    for cell, sid in assignments.items():
        placement_rows.append({
            "cell_name": cell,
            "site_id": sid,
            "x_um": float(sites_df.at[sid, "x_um"]),  # type: ignore[arg-type]
            "y_um": float(sites_df.at[sid, "y_um"]),  # type: ignore[arg-type]
        })
    placement_df = pd.DataFrame(placement_rows)
    
    return updated_pins, placement_df


if __name__ == "__main__":
    fabric_file_path = "inputs/Platform/fabric.yaml"
    fabric_cells_file_path = "inputs/Platform/fabric_cells.yaml"
    pins_file_path = "inputs/Platform/pins.yaml"
    netlist_file_path = "inputs/designs/arith_mapped.json"

    # Extract design name from netlist file path
    # e.g., "inputs/designs/arith_mapped.json" -> "arith"
    design_name = Path(netlist_file_path).stem.replace("_mapped", "")
    
    fabric, fabric_df = get_fabric_db(fabric_file_path, fabric_cells_file_path)
    pins_df, pins_meta = load_pins_df(pins_file_path)
    ports_df, netlist_graph = get_netlist_graph(netlist_file_path)
    
    print(f"Running placement for design: {design_name}")
    print(f"Total cells to place: {len(netlist_graph['cell_name'].unique())}")
    
    # Time overall placement
    placement_start = time.time()
    assigned_pins, placement_df = place_cells_greedy_sim_anneal(
        fabric=fabric,
        fabric_df=fabric_df,
        pins_df=pins_df,
        ports_df=ports_df,
        netlist_graph=netlist_graph,
    )
    placement_end = time.time()
    total_time_with_overhead = placement_end - placement_start
    
    # Debug: Print overall timing (includes DataFrame building overhead)
    print(f"\n[DEBUG] Overall Timing (including overhead):")
    print(f"  Total time: {total_time_with_overhead:.3f} seconds")

    # Create build directory if it doesn't exist
    build_dir = Path("build") / design_name
    build_dir.mkdir(parents=True, exist_ok=True)
    
    # Write placement DataFrame to CSV
    output_csv = build_dir / f"{design_name}_placement.csv"
    placement_df.to_csv(output_csv, index=False)
    
    print(f"\nPlacement complete!")
    print(f"Placed {len(placement_df)} cells")
    print(f"Output written to: {output_csv}")
