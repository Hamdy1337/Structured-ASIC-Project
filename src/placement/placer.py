"""
placer.py: Main module for assigning cells on the sASIC fabric.
    python -m src.placement.placer
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Any, Optional, Set
from pathlib import Path
import time
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from src.parsers.fabric_db import get_fabric_db, Fabric
from src.parsers.pins_parser import load_and_validate as load_pins_df
from src.parsers.netlist_parser import get_netlist_graph
from src.parsers.fabric_cells_parser import parse_fabric_cells_file
from src.placement.placement_mapper import map_placement_to_physical_cells, generate_map_file

from src.placement.port_assigner import assign_ports_to_pins
from src.placement.dependency_levels import build_dependency_levels
from src.placement.placement_utils import (
    build_sites,
    fixed_points_from_pins,
    nets_by_cell,
    in_out_nets_by_cell,
    median,
    nearest_site,
    build_spatial_index,
    driver_points,
    hpwl_for_nets,
)
from src.placement.simulated_annealing import anneal_batch
from src.validation.placement_validator import validate_placement, print_validation_report
from src.placement.simulated_annealing import anneal_batch
from src.validation.placement_validator import validate_placement, print_validation_report
from src.Visualization.heatmap import plot_placement_heatmap
from src.placement.placement_utils import hpwl_for_nets, nets_by_cell, fixed_points_from_pins



def generate_net_hpwl_histogram(
    placement_df: pd.DataFrame,
    updated_pins: pd.DataFrame,
    netlist_graph: pd.DataFrame,
    design_name: str,
    build_dir: Path,
    bins: int = 50,
) -> None:
    """
    Compute HPWL for every net in the design (using in-memory data) and
    save a 1D histogram:

        build/<design_name>/<design_name>_net_length.png
    """

    # 1) Rebuild levelized view so we can reuse nets_by_cell()
    g_levels_hist = build_dependency_levels(updated_pins, netlist_graph)
    cell_to_nets = nets_by_cell(g_levels_hist)

    # 2) Fixed IO points per net (from pins)
    fixed_pts = fixed_points_from_pins(updated_pins)

    # 3) Cell positions: cell_name -> (x, y)
    pos_cells: Dict[str, Tuple[float, float]] = {
        str(row.cell_name): (float(row.x_um), float(row.y_um))
        for row in placement_df.itertuples(index=False)
    }

    # 4) Collect all nets that appear either on cells or on fixed pins
    all_nets: set[int] = set()
    for nets in cell_to_nets.values():
        all_nets.update(nets)
    all_nets.update(fixed_pts.keys())

    # 5) Compute HPWL per net (reuse hpwl_for_nets for each single-net set)
    hpwl_values: List[float] = []
    for nb in sorted(all_nets):
        val = hpwl_for_nets({nb}, pos_cells, cell_to_nets, fixed_pts)
        if val > 0.0:
            hpwl_values.append(val)

    if not hpwl_values:
        print("[DEBUG] Net HPWL histogram: no nets to plot, skipping.")
        return

    hp = np.array(hpwl_values, dtype=float)

    total_nets = hp.size
    mean = float(hp.mean())
    median = float(np.median(hp))
    hp_min = float(hp.min())
    hp_max = float(hp.max())
    total_hpwl = float(hp.sum())

    build_dir.mkdir(parents=True, exist_ok=True)
    out_path = build_dir / f"{design_name}_net_length.png"

    # 6) Plot histogram
    fig, ax = plt.subplots(figsize=(10, 5))
    counts, bin_edges, patches = ax.hist(hp, bins=bins)

    # Color bars (viridis like your example)
    cmap = plt.get_cmap("viridis")
    n_patches = max(len(patches) - 1, 1)
    for i, p in enumerate(patches):
        p.set_facecolor(cmap(i / n_patches))

    ax.set_xlabel("Net HPWL (um)")
    ax.set_ylabel("Number of Nets")
    ax.set_title(f"Net Length Distribution - {design_name}")

    stats_text = (
        f"Total nets: {total_nets:,}\n"
        f"Mean HPWL: {mean:.2f} um\n"
        f"Median HPWL: {median:.2f} um\n"
        f"Min HPWL: {hp_min:.2f} um\n"
        f"Max HPWL: {hp_max:.2f} um\n"
        f"Total HPWL: {total_hpwl:,.2f} um"
    )

    ax.text(
        0.98,
        0.98,
        stats_text,
        transform=ax.transAxes,
        va="top",
        ha="right",
        fontsize=9,
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
    )

    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)

    print(f"[DEBUG] Net length (HPWL) histogram written to: {out_path}")

    
def place_cells_greedy_sim_anneal(
    fabric: Fabric,
    fabric_df: pd.DataFrame,
    pins_df: pd.DataFrame,
    ports_df: pd.DataFrame,
    netlist_graph: pd.DataFrame,
    sa_moves_per_temp: int = 5000,
    sa_cooling_rate: float = 0.95,
    sa_T_initial: Optional[float] = None,
    sa_p_refine: float = 0.7,
    sa_p_explore: float = 0.3,
    sa_refine_max_distance: float = 100.0,
    sa_W_initial: float = 0.1,
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
        (updated_pins_df, placement_df, validation_result)
        where validation_result is a PlacementValidationResult object
    """
    t_total_start = time.perf_counter()
    
    # ---- Phase 1: Port Assignment (Seeding) ----
    print("[DEBUG] === PHASE 1: PORT ASSIGNMENT (SEEDING) ===")
    t_seeding_start = time.perf_counter()
    updated_pins, _assign = assign_ports_to_pins(pins_df, ports_df)
    t_seeding_end = time.perf_counter()
    seeding_dur = t_seeding_end - t_seeding_start
    print(f"[DEBUG] Port assignment (seeding) completed in {seeding_dur:.3f}s")
    print(f"[DEBUG] Assigned {len(_assign)} port-to-pin mappings")
    print()

    # ---- Phase 2: Build Sites and Spatial Index ----
    print("[DEBUG] === PHASE 2: BUILDING SITES AND SPATIAL INDEX ===")
    t_build_start = time.perf_counter()
    sites_df = build_sites(fabric_df)
    print(f"[DEBUG] Built {len(sites_df)} sites from fabric")
    # Build spatial index (grid + arrays)
    site_x, site_y, is_free, site_type_arr, minx, miny, cell_w, cell_h, gx, gy, bins = build_spatial_index(sites_df)
    t_build_end = time.perf_counter()
    build_sites_dur = t_build_end - t_build_start
    print(f"[DEBUG] Spatial index built: grid={gx}x{gy}, {len(sites_df)} sites")
    print(f"[DEBUG] Building sites and spatial index completed in {build_sites_dur:.3f}s")
    print()

    # ---- Phase 3: Build Fixed Points ----
    print("[DEBUG] === PHASE 3: BUILDING FIXED POINTS ===")
    t_fixed_start = time.perf_counter()
    fixed_pts = fixed_points_from_pins(updated_pins)
    t_fixed_end = time.perf_counter()
    fixed_dur = t_fixed_end - t_fixed_start
    print(f"[DEBUG] Built {len(fixed_pts)} fixed point nets")
    print(f"[DEBUG] Building fixed points completed in {fixed_dur:.3f}s")
    print()

    # ---- Phase 4: Build Dependency Levels (Levelization) ----
    print("[DEBUG] === PHASE 4: BUILDING DEPENDENCY LEVELS (LEVELIZATION) ===")
    t_level_start = time.perf_counter()
    g_levels = build_dependency_levels(updated_pins, netlist_graph)
    t_level_end = time.perf_counter()
    levelize_dur = t_level_end - t_level_start
    unique_levels = sorted(g_levels["dependency_level"].unique())
    print(f"[DEBUG] Levelization completed: {len(unique_levels)} levels found")
    print(f"[DEBUG] Building dependency levels completed in {levelize_dur:.3f}s")
    print()

    # ---- Phase 5: Build Cell Mappings ----
    print("[DEBUG] === PHASE 5: BUILDING CELL MAPPINGS ===")
    t_mapping_start = time.perf_counter()
    ins_by_cell, outs_by_cell = in_out_nets_by_cell(g_levels)
    cell_to_nets = nets_by_cell(g_levels)
    t_mapping_end = time.perf_counter()
    mapping_dur = t_mapping_end - t_mapping_start
    print(f"[DEBUG] Built mappings for {len(ins_by_cell)} cells")
    print(f"[DEBUG] Building cell mappings completed in {mapping_dur:.3f}s")
    print()
    
    # ---- Phase 6: Build Cell Type Mapping ----
    print("[DEBUG] === PHASE 6: BUILDING CELL TYPE MAPPING ===")
    t_type_start = time.perf_counter()
    cell_type_by_cell: Dict[str, Optional[str]] = {}
    if "cell_type" in g_levels.columns:
        for cell, grp in g_levels.groupby("cell_name"):  # type: ignore[arg-type]
            ctype_vals = grp["cell_type"].dropna().astype(str).unique().tolist()
            cell_type_by_cell[str(cell)] = ctype_vals[0] if ctype_vals else None
        print(f"[DEBUG] Built cell type mapping for {len(cell_type_by_cell)} cells")
    else:
        print("[DEBUG] No cell_type column found, skipping cell type mapping")
    t_type_end = time.perf_counter()
    type_dur = t_type_end - t_type_start
    print(f"[DEBUG] Building cell type mapping completed in {type_dur:.3f}s")
    print()

    # ---- Phase 7: Order Cells by Level ----
    print("[DEBUG] === PHASE 7: ORDERING CELLS BY LEVEL ===")
    t_order_start = time.perf_counter()
    cell_levels = g_levels[["cell_name", "dependency_level"]].drop_duplicates()
    order = cell_levels.sort_values(by=["dependency_level", "cell_name"]).reset_index(drop=True)
    t_order_end = time.perf_counter()
    order_dur = t_order_end - t_order_start
    total_cells = len(order)
    print(f"[DEBUG] Ordered {total_cells} cells across {len(unique_levels)} levels")
    print(f"[DEBUG] Ordering cells completed in {order_dur:.3f}s")
    print()

    # Placement state
    assignments: Dict[str, int] = {}  # cell_name -> site_id
    pos_cells: Dict[str, Tuple[float, float]] = {}

    # Helper to get driver/source points for a cell
    def _driver_points(cell: str) -> List[Tuple[float, float]]:
        pts: List[Tuple[float, float]] = []
        for nb in ins_by_cell.get(cell, set()):
            # Placed driver cell on this net?
            for other, pos in pos_cells.items():
                if nb in outs_by_cell.get(other, set()):
                    pts.append(pos)
            # Top-level pins on this net
            pts.extend(fixed_pts.get(nb, []))
        return pts

    # ---- Phase 8: Level-by-Level Placement (Growing) ----
    print("[DEBUG] === PHASE 8: LEVEL-BY-LEVEL PLACEMENT (GROWING) ===")
    batch_size = 120
    greedy_time_total = 0.0
    sa_time_total = 0.0
    per_level_times: List[Tuple[int, float, float]] = []
    total_levels = len(unique_levels)
    
    for level_idx, lvl in enumerate(sorted(unique_levels), 1):
        print(f"[DEBUG] --- Processing Level {lvl} ({level_idx}/{total_levels}) ---")
        t_level_processing_start = time.perf_counter()
        
        # Build list of cell names (already strings or convertible)
        cells_series: pd.Series = order.loc[order["dependency_level"] == lvl, "cell_name"]  # type: ignore[assignment]
        level_cells = [str(x) for x in cells_series.tolist()]
        print(f"[DEBUG] Level {lvl}: {len(level_cells)} cells to place")
        
        # ---- Greedy initial placement for level ----
        print(f"[DEBUG] Level {lvl}: Starting greedy placement...")
        t_greedy_start = time.perf_counter()
        placed_count = 0
        total_cells = len(level_cells)
        # Progress reporting: every 10% or every 50 cells, whichever is smaller
        progress_interval = max(1, min(50, total_cells // 10))
        last_progress = 0
        
        for cell_idx, c in enumerate(level_cells, 1):
            # Compute target (median of driver points)
            pts = _driver_points(c)
            if pts:
                tx = median([p[0] for p in pts])
                ty = median([p[1] for p in pts])
            else:
                # fallback: center of available sites
                tx = float(sites_df["x_um"].median())
                ty = float(sites_df["y_um"].median())
            required_type = cell_type_by_cell.get(c)
            sid = nearest_site((tx, ty), is_free, sites_df, site_x, site_y, site_type_arr, minx, miny, cell_w, cell_h, gx, gy, bins, required_type=required_type)
            if sid is None:
                continue
            assignments[c] = sid
            pos_x = float(sites_df.at[sid, "x_um"])  # type: ignore[arg-type]
            pos_y = float(sites_df.at[sid, "y_um"])  # type: ignore[arg-type]
            pos_cells[c] = (pos_x, pos_y)
            # consume site
            is_free[sid] = False
            placed_count += 1
            
            # Progress reporting
            if cell_idx - last_progress >= progress_interval or cell_idx == total_cells:
                pct = (cell_idx / total_cells) * 100
                print(f"[PROGRESS] L{lvl}: {cell_idx}/{total_cells} ({pct:.1f}%) | Placed: {placed_count} | Latest: {c[:20]} @ ({pos_x:.1f}, {pos_y:.1f})", flush=True)
                last_progress = cell_idx
        
        t_greedy_end = time.perf_counter()
        greedy_time = t_greedy_end - t_greedy_start
        greedy_time_total += greedy_time
        print(f"[DEBUG] Level {lvl}: Greedy placement completed - placed {placed_count}/{len(level_cells)} cells in {greedy_time:.3f}s")
        
        # (SA moved to global phase after all levels are placed)
        per_level_times.append((int(lvl), greedy_time, 0.0))
        
        t_level_processing_end = time.perf_counter()
        level_total_time = t_level_processing_end - t_level_processing_start
        print(f"[DEBUG] Level {lvl}: Total level processing time: {level_total_time:.3f}s (greedy only)")
        print(f"[PROGRESS] L{lvl}: COMPLETE | Total placed so far: {len(assignments)} cells", flush=True)
        print()

    # Calculate Greedy HPWL
    all_nets = set()
    for ns in cell_to_nets.values():
        all_nets.update(ns)
    greedy_hpwl = hpwl_for_nets(all_nets, pos_cells, cell_to_nets, fixed_pts)
    print(f"[DEBUG] Greedy Placement HPWL: {greedy_hpwl:.3f}")

    # ---- Phase 8.5: Global Simulated Annealing ----
    print("[DEBUG] === PHASE 8.5: GLOBAL SIMULATED ANNEALING ===")
    t_sa_start = time.perf_counter()
    num_batches = 0
    
    # Collect all placed cells
    all_placed_cells = list(assignments.keys())
    
    # Pre-compute net_to_cells mapping for SA optimization
    # This is static (connectivity doesn't change), so we compute it once
    print("[DEBUG] Pre-computing net_to_cells map for SA...")
    net_to_cells: Dict[int, List[str]] = {}
    for cell in all_placed_cells:
        for net in cell_to_nets.get(cell, set()):
            net_to_cells.setdefault(net, []).append(cell)
    
    # Group cells by type for efficient batching
    cells_by_type: Dict[Optional[str], List[str]] = {}
    for c in all_placed_cells:
        cell_type = cell_type_by_cell.get(c)
        cells_by_type.setdefault(cell_type, []).append(c)
    
    # Calculate total batches
    total_batches = sum((len(type_cells) + batch_size - 1) // batch_size 
                       for type_cells in cells_by_type.values() 
                       if len(type_cells) >= 2)
    
    print(f"[DEBUG] Global SA: Processing {len(all_placed_cells)} cells in {total_batches} batches")

    for cell_type, type_cells in cells_by_type.items():
        if len(type_cells) < 2:
            continue
        
        type_name = str(cell_type) if cell_type is not None else "unknown"
        
        # Batch cells
        for i in range(0, len(type_cells), batch_size):
            batch = type_cells[i:i + batch_size]
            if len(batch) < 2:
                continue
            num_batches += 1
            if num_batches % 10 == 0 or num_batches == total_batches:
                 print(f"[PROGRESS] Global SA: Batch {num_batches}/{total_batches} ({len(batch)} cells, type: {type_name[:20]})", flush=True)
            
            anneal_batch(
                batch, pos_cells, assignments, sites_df, cell_to_nets, fixed_pts,
                iters=sa_moves_per_temp,
                alpha=sa_cooling_rate,
                T_initial=sa_T_initial,
                p_refine=sa_p_refine,
                p_explore=sa_p_explore,
                refine_max_distance=sa_refine_max_distance,
                W_initial=sa_W_initial,
                seed=sa_seed,
                cell_types=cell_type_by_cell,
                net_to_cells=net_to_cells
            )

    t_sa_end = time.perf_counter()
    sa_time_total = t_sa_end - t_sa_start
    print(f"[DEBUG] Global SA completed in {sa_time_total:.3f}s")
    
    # Calculate SA HPWL
    sa_hpwl = hpwl_for_nets(all_nets, pos_cells, cell_to_nets, fixed_pts)
    print(f"[DEBUG] SA Refined HPWL: {sa_hpwl:.3f}")
    if greedy_hpwl > 0:
        print(f"[DEBUG] HPWL Improvement: {greedy_hpwl - sa_hpwl:.3f} ({(greedy_hpwl - sa_hpwl)/greedy_hpwl*100:.2f}%)")
    print()

    # ---- Phase 9: Build Placement DataFrame ----
    print("[DEBUG] === PHASE 9: BUILDING PLACEMENT DATAFRAME ===")
    t_df_start = time.perf_counter()
    placement_rows: List[Dict[str, Any]] = []
    total_assigned = len(assignments)
    df_progress_interval = max(1, total_assigned // 10)  # Report every 10%
    df_last_progress = 0
    
    for df_idx, (cell, sid) in enumerate(assignments.items(), 1):
        placement_rows.append({
            "cell_name": cell,
            "site_id": sid,
            "x_um": float(sites_df.at[sid, "x_um"]),  # type: ignore[arg-type]
            "y_um": float(sites_df.at[sid, "y_um"]),  # type: ignore[arg-type]
        })
        
        # Progress reporting
        if df_idx - df_last_progress >= df_progress_interval or df_idx == total_assigned:
            pct = (df_idx / total_assigned) * 100
            print(f"[PROGRESS] Building DataFrame: {df_idx}/{total_assigned} ({pct:.1f}%)", flush=True)
            df_last_progress = df_idx
    
    placement_df = pd.DataFrame(placement_rows)
    t_df_end = time.perf_counter()
    df_dur = t_df_end - t_df_start
    print(f"[DEBUG] Built placement DataFrame with {len(placement_df)} cells")
    print(f"[DEBUG] Building placement DataFrame completed in {df_dur:.3f}s")
    print()
    
    # ---- Phase 10: Validate Placement ----
    print("[DEBUG] === PHASE 10: VALIDATING PLACEMENT ===")
    t_validate_start = time.perf_counter()
    validation_result = validate_placement(
        placement_df=placement_df,
        netlist_graph=netlist_graph,
        sites_df=sites_df,
        assignments_df=_assign,
        ports_df=ports_df,
        pins_df=pins_df,
        updated_pins=updated_pins,
        fabric_df=fabric_df,
    )
    t_validate_end = time.perf_counter()
    validate_dur = t_validate_end - t_validate_start
    print(f"[DEBUG] Validation completed in {validate_dur:.3f}s")
    print_validation_report(validation_result)
    print()

    # ---- Final Timing Summary ----
    t_total_end = time.perf_counter()
    total_dur = t_total_end - t_total_start
    print("[DEBUG] ========================================")
    print("[DEBUG] FINAL TIMING SUMMARY")
    print("[DEBUG] ========================================")
    print(f"[DEBUG] Phase 1 - Port Assignment (Seeding):     {seeding_dur:.3f}s ({seeding_dur/total_dur*100:.1f}%)")
    print(f"[DEBUG] Phase 2 - Build Sites & Spatial Index:    {build_sites_dur:.3f}s ({build_sites_dur/total_dur*100:.1f}%)")
    print(f"[DEBUG] Phase 3 - Build Fixed Points:             {fixed_dur:.3f}s ({fixed_dur/total_dur*100:.1f}%)")
    print(f"[DEBUG] Phase 4 - Build Dependency Levels:      {levelize_dur:.3f}s ({levelize_dur/total_dur*100:.1f}%)")
    print(f"[DEBUG] Phase 5 - Build Cell Mappings:           {mapping_dur:.3f}s ({mapping_dur/total_dur*100:.1f}%)")
    print(f"[DEBUG] Phase 6 - Build Cell Type Mapping:       {type_dur:.3f}s ({type_dur/total_dur*100:.1f}%)")
    print(f"[DEBUG] Phase 7 - Order Cells by Level:         {order_dur:.3f}s ({order_dur/total_dur*100:.1f}%)")
    print(f"[DEBUG] Phase 8 - Level-by-Level Placement:      {greedy_time_total + sa_time_total:.3f}s ({(greedy_time_total + sa_time_total)/total_dur*100:.1f}%)")
    print(f"[DEBUG]   - Greedy Placement:                   {greedy_time_total:.3f}s ({greedy_time_total/total_dur*100:.1f}%)")
    print(f"[DEBUG]   - Simulated Annealing:                 {sa_time_total:.3f}s ({sa_time_total/total_dur*100:.1f}%)")
    print(f"[DEBUG] Phase 9 - Build Placement DataFrame:     {df_dur:.3f}s ({df_dur/total_dur*100:.1f}%)")
    print(f"[DEBUG] Phase 10 - Validate Placement:           {validate_dur:.3f}s ({validate_dur/total_dur*100:.1f}%)")
    print(f"[DEBUG] TOTAL TIME:                              {total_dur:.3f}s")
    print("[DEBUG] ========================================")
    print()
    
    # Legacy timing output (for compatibility)
    print(f"[PlacerTiming] total={total_dur:.3f}s build_sites={build_sites_dur:.3f}s levelize={levelize_dur:.3f}s greedy={greedy_time_total:.3f}s sa={sa_time_total:.3f}s")
    print(f"PLACER_SUMMARY total={total_dur:.3f}s greedy={greedy_time_total:.3f}s sa={sa_time_total:.3f}s")
    if per_level_times:
        top_levels = sorted(per_level_times, key=lambda x: x[2], reverse=True)[:5]
        for lvl_id, g_t, sa_t in top_levels:
            print(f"[PlacerTiming] level={lvl_id} greedy={g_t:.3f}s sa={sa_t:.3f}s")
    # Print Greedy and SA Total HPWL
    print(f"[DEBUG] Final Greedy HPWL: {greedy_hpwl:.3f}")
    print(f"[DEBUG] Final SA HPWL: {sa_hpwl:.3f}")
    print(f"[DEBUG] Overall HPWL Improvement: {greedy_hpwl - sa_hpwl:.3f} ({(greedy_hpwl - sa_hpwl)/greedy_hpwl*100:.2f}%)")
    
    return updated_pins, placement_df, validation_result, sa_hpwl




def run_placement(design_name: str = "arith") -> None:
    # Define project root (assuming this file is in src/placement/)
    project_root = Path(__file__).resolve().parent.parent.parent
    
    fabric_file_path = project_root / "inputs/Platform/fabric.yaml"
    fabric_cells_file_path = project_root / "inputs/Platform/fabric_cells.yaml"
    pins_file_path = project_root / "inputs/Platform/pins.yaml"
    netlist_file_path = project_root / f"inputs/designs/{design_name}_mapped.json"

    if not netlist_file_path.exists():
        print(f"Error: Netlist file not found: {netlist_file_path}")
        return

    fabric, fabric_df = get_fabric_db(str(fabric_file_path), str(fabric_cells_file_path))
    # Parse fabric_cells once to reuse for mapping (avoid re-parsing)
    _, fabric_cells_df = parse_fabric_cells_file(str(fabric_cells_file_path))
    pins_df, pins_meta = load_pins_df(str(pins_file_path))
    ports_df, netlist_graph = get_netlist_graph(str(netlist_file_path))
    
    print(f"Running placement for design: {design_name}")
    print(f"Total cells to place: {len(netlist_graph['cell_name'].unique())}")
    
    # Time overall placement
    placement_start = time.time()
    assigned_pins, placement_df, validation_result, _ = place_cells_greedy_sim_anneal(
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
    build_dir = project_root / "build" / design_name
    build_dir.mkdir(parents=True, exist_ok=True)
    
    # Map placement coordinates to physical cell names and add cell types
    print(f"\n[DEBUG] Mapping placement to physical cell names...")
    placement_df = map_placement_to_physical_cells(
        placement_df=placement_df,
        fabric_cells_df=fabric_cells_df,
        fabric_df=fabric_df,
        coord_tolerance=0.001
    )
    
    # Write placement DataFrame to CSV
    output_csv = build_dir / f"{design_name}_placement.csv"
    placement_df.to_csv(output_csv, index=False)

    generate_net_hpwl_histogram(
        placement_df=placement_df,
        updated_pins=assigned_pins,
        netlist_graph=netlist_graph,
        design_name=design_name,
        build_dir=build_dir,
        bins=50,  # adjust if you want more/less bins
    )
    
    print(f"\nPlacement complete!")
    print(f"Placed {len(placement_df)} cells")
    print(f"Output written to: {output_csv}")
    
    # Generate .map file (standard format for CTS: logical_name physical_name)
    map_file_path = build_dir / f"{design_name}.map"
    generate_map_file(
        placement_df=placement_df,
        map_file_path=map_file_path,
        design_name=design_name
    )
    
    # Generate placement heatmap
    print(f"\n[DEBUG] Generating placement heatmap...")
    heatmap_output = build_dir / f"{design_name}_placement_heatmap.png"
    plot_placement_heatmap(
        data=output_csv,  # Use the CSV file that was just written
        bins=80,
        cmap="viridis",
        figsize=(12, 10),
        output_path=heatmap_output,
        title=f"Placement Density Heatmap - {design_name} ({len(placement_df)} cells)",
    )

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        design = sys.argv[1]
    else:
        design = "arith"
    run_placement(design)
    
    # Calculate additional statistics for summary
    total_cells_netlist = len(netlist_graph['cell_name'].unique())
    placed_cells_count = len(placement_df)
    placement_rate = (placed_cells_count / total_cells_netlist * 100) if total_cells_netlist > 0 else 0.0
    
    # Build data structures for HPWL calculation
    pos_cells: Dict[str, Tuple[float, float]] = {}
    for _, row in placement_df.iterrows():
        pos_cells[row['cell_name']] = (row['x_um'], row['y_um'])
    cell_to_nets = nets_by_cell(netlist_graph)
    fixed_pts = fixed_points_from_pins(assigned_pins)
    
    # Get all nets
    all_nets_set: Set[int] = set()
    for nets in cell_to_nets.values():
        all_nets_set |= nets
    all_nets_set |= set(fixed_pts.keys())
    num_nets = len(all_nets_set)
    
    # Get HPWL from validation result or calculate it
    total_hpwl = validation_result.stats.get('total_hpwl', None)
    if total_hpwl is None:
        # Calculate HPWL if not in validation result
        try:
            total_hpwl = hpwl_for_nets(all_nets_set, pos_cells, cell_to_nets, fixed_pts)
        except Exception as e:
            total_hpwl = None
    
    # Calculate HPWL per net to find min/max
    min_hpwl_per_net = None
    max_hpwl_per_net = None
    if total_hpwl is not None:
        try:
            # Calculate HPWL for each net individually
            hpwl_per_net: List[float] = []
            for nb in all_nets_set:
                xs: List[float] = []
                ys: List[float] = []
                # Placed cells contributing to this net
                for cell, pos in pos_cells.items():
                    if nb in cell_to_nets.get(cell, set()):
                        xs.append(pos[0])
                        ys.append(pos[1])
                # Fixed pins on this net
                for (fx, fy) in fixed_pts.get(nb, []):
                    xs.append(fx)
                    ys.append(fy)
                if len(xs) >= 2:
                    net_hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
                    hpwl_per_net.append(net_hpwl)
            
            if hpwl_per_net:
                min_hpwl_per_net = min(hpwl_per_net)
                max_hpwl_per_net = max(hpwl_per_net)
        except Exception:
            # If calculation fails, leave as None
            pass
    
    avg_hpwl_per_net = (total_hpwl / num_nets) if (total_hpwl is not None and num_nets > 0) else None
    
    # Rebuild sites_df for statistics (needed for site count and die dimensions)
    sites_df_summary = build_sites(fabric_df)
    total_sites = len(sites_df_summary)
    site_utilization = validation_result.stats.get('site_utilization_percent', 
                                                   (placed_cells_count / total_sites * 100) if total_sites > 0 else 0.0)
    
    # Get placement bounds
    placement_bounds = validation_result.stats.get('placement_bounds', {})
    x_span = validation_result.stats.get('x_span', None)
    y_span = validation_result.stats.get('y_span', None)
    
    # Get die dimensions from fabric
    die_width = float(sites_df_summary["x_um"].max()) if len(sites_df_summary) > 0 else 0.0
    die_height = float(sites_df_summary["y_um"].max()) if len(sites_df_summary) > 0 else 0.0
    
    # Calculate cells per second
    cells_per_sec = (placed_cells_count / total_time_with_overhead) if total_time_with_overhead > 0 else 0.0
    
    # Print comprehensive statistics summary
    print("\n" + "="*80)
    print(" " * 25 + "PLACEMENT SUMMARY")
    print("="*80)
    
    # Design Information
    print("\n📋 DESIGN INFORMATION")
    print(f"  Design Name:              {design_name}")
    print(f"  Total Cells (Netlist):    {total_cells_netlist:,}")
    print(f"  Cells Successfully Placed: {placed_cells_count:,}")
    print(f"  Placement Success Rate:   {placement_rate:.1f}%")
    if validation_result.stats.get('missing_cells', 0) > 0:
        print(f"  ⚠️  Missing Cells:         {validation_result.stats.get('missing_cells', 0):,}")
    
    # Placement Quality
    print("\n📊 PLACEMENT QUALITY")
    if total_hpwl is not None:
        print(f"  Total HPWL:               {total_hpwl:,.2f} μm")
        if avg_hpwl_per_net is not None:
            print(f"  Average HPWL per Net:     {avg_hpwl_per_net:.2f} μm")
        if min_hpwl_per_net is not None and max_hpwl_per_net is not None:
            print(f"  Min HPWL per Net:         {min_hpwl_per_net:.2f} μm")
            print(f"  Max HPWL per Net:         {max_hpwl_per_net:.2f} μm")
    else:
        print(f"  Total HPWL:               (calculation failed)")
    print(f"  Number of Nets:           {num_nets:,}")
    print(f"  Site Utilization:         {site_utilization:.1f}% ({placed_cells_count:,}/{total_sites:,} sites)")
    
    # Spatial Distribution
    print("\n🗺️  SPATIAL DISTRIBUTION")
    print(f"  Die Dimensions:           {die_width:.1f} × {die_height:.1f} μm")
    if x_span is not None and y_span is not None:
        print(f"  Placement Span:           {x_span:.1f} × {y_span:.1f} μm")
        if placement_bounds:
            print(f"  Placement Bounds:         X=[{placement_bounds.get('x_min', 0):.1f}, {placement_bounds.get('x_max', 0):.1f}] μm")
            print(f"                            Y=[{placement_bounds.get('y_min', 0):.1f}, {placement_bounds.get('y_max', 0):.1f}] μm")
    
    # Performance Metrics
    print("\n⚡ PERFORMANCE METRICS")
    print(f"  Total Placement Time:     {total_time_with_overhead:.3f} seconds")
    print(f"  Cells per Second:         {cells_per_sec:.1f} cells/sec")
    
    # Validation Status
    print("\n✅ VALIDATION STATUS")
    if validation_result.passed:
        print(f"  Status:                   ✓ PASSED")
    else:
        print(f"  Status:                   ✗ FAILED")
    if len(validation_result.errors) > 0:
        print(f"  Errors:                   {len(validation_result.errors)}")
    if len(validation_result.warnings) > 0:
        print(f"  Warnings:                 {len(validation_result.warnings)}")
    
    print("\n" + "="*80)
    
    # Check validation result
    if not validation_result.passed:
        print("\n⚠️  WARNING: Placement validation found errors (see report above)")
        # Uncomment below to exit on validation failure:
        # import sys
        # sys.exit(1)
    else:
        print("\n✅ Placement validation passed!")
