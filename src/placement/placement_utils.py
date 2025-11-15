"""
placement_utils.py: Utility functions for cell placement.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Set, Optional
import pandas as pd


def build_sites(df: pd.DataFrame) -> pd.DataFrame:
    """Build site list from fabric DataFrame.
    
    Uses cell_x/cell_y as site coordinates; drops duplicates.
    Returns DataFrame with columns: site_id, x_um, y_um, tile_name, cell_type.
    """
    cols = [c for c in ["cell_x", "cell_y", "tile_name", "cell_type"] if c in df.columns]
    sites = df[cols].drop_duplicates().reset_index(drop=True).copy()
    sites.rename(columns={"cell_x": "x_um", "cell_y": "y_um"}, inplace=True)
    sites.insert(0, "site_id", range(len(sites)))  # type: ignore[arg-type]
    return sites


def fixed_points_from_pins(pins: pd.DataFrame) -> Dict[int, List[Tuple[float, float]]]:
    """Extract fixed pin positions by net_bit.
    
    Returns dict mapping net_bit -> list of (x, y) positions.
    """
    fp: Dict[int, List[Tuple[float, float]]] = {}
    if not {"net_bit", "x_um", "y_um"}.issubset(pins.columns):
        return fp
    pins_valid = pins.dropna(subset=["net_bit", "x_um", "y_um"]).copy()  # type: ignore[call-arg]
    for row in pins_valid.itertuples(index=False):
        nb = int(getattr(row, "net_bit"))
        x = float(getattr(row, "x_um"))
        y = float(getattr(row, "y_um"))
        fp.setdefault(nb, []).append((x, y))
    return fp


def nets_by_cell(gdf: pd.DataFrame) -> Dict[str, Set[int]]:
    """Build mapping of cell_name -> set of all net_bits connected to that cell."""
    res: Dict[str, Set[int]] = {}
    for cell, grp in gdf.groupby("cell_name"):  # type: ignore[arg-type]
        nets = set(grp["net_bit"].dropna().astype(int).tolist())
        res[str(cell)] = nets
    return res


def in_out_nets_by_cell(gdf: pd.DataFrame) -> Tuple[Dict[str, Set[int]], Dict[str, Set[int]]]:
    """Build separate mappings for input and output nets per cell.
    
    Returns:
        (ins_by_cell, outs_by_cell) where each maps cell_name -> set of net_bits
    """
    ins: Dict[str, Set[int]] = {}
    outs: Dict[str, Set[int]] = {}
    dir_l = gdf["direction"].astype(str).str.lower()
    for cell, grp in gdf.groupby("cell_name"):  # type: ignore[arg-type]
        in_n = set(grp.loc[dir_l.loc[grp.index] == "input", "net_bit"].dropna().astype(int).tolist())
        out_n = set(grp.loc[dir_l.loc[grp.index] == "output", "net_bit"].dropna().astype(int).tolist())
        ins[str(cell)] = in_n
        outs[str(cell)] = out_n
    return ins, outs


def median(vals: List[float]) -> float:
    """Calculate median of a list of values."""
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def nearest_site(target: Tuple[float, float], free_site_ids: List[int], sites_df: pd.DataFrame) -> Optional[int]:
    """Find nearest free site to target position.
    
    Uses Manhattan distance (L1) as primary, Euclidean (L2) as tie-breaker.
    Returns site_id or None if no free sites.
    """
    import math
    if not free_site_ids:
        return None
    tx, ty = target
    best = None
    best_key = (float("inf"), float("inf"))
    for sid in free_site_ids:
        sx = float(sites_df.at[sid, "x_um"])  # type: ignore[index, arg-type]
        sy = float(sites_df.at[sid, "y_um"])  # type: ignore[index, arg-type]
        l1 = abs(tx - sx) + abs(ty - sy)
        l2 = math.hypot(tx - sx, ty - sy)
        key = (l1, l2)
        if key < best_key:
            best_key = key
            best = sid
    return best


def hpwl_for_nets(
    nets: Set[int],
    pos_cells: Dict[str, Tuple[float, float]],
    cell_to_nets: Dict[str, Set[int]],
    fixed_pts: Dict[int, List[Tuple[float, float]]]
) -> float:
    """Calculate Half-Perimeter Wire Length (HPWL) for a set of nets.
    
    For each net, computes bounding box of all connected cells and fixed pins,
    then sums the half-perimeter (width + height) of each bounding box.
    
    Returns total HPWL in microns.
    """
    total = 0.0
    for nb in nets:
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
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


def driver_points(
    cell: str,
    ins_by_cell: Dict[str, Set[int]],
    outs_by_cell: Dict[str, Set[int]],
    pos_cells: Dict[str, Tuple[float, float]],
    fixed_pts: Dict[int, List[Tuple[float, float]]]
) -> List[Tuple[float, float]]:
    """Get all driver/source positions for a cell's input nets.
    
    Returns list of (x, y) positions from:
    - Placed cells that drive this cell's inputs
    - Fixed pins on this cell's input nets
    """
    pts: List[Tuple[float, float]] = []
    input_nets = ins_by_cell.get(cell, set())
    for nb in input_nets:
        # Placed driver cell on this net?
        for other, pos in pos_cells.items():
            if nb in outs_by_cell.get(other, set()):
                pts.append(pos)
        # Top-level pins on this net
        pts.extend(fixed_pts.get(nb, []))
    return pts

