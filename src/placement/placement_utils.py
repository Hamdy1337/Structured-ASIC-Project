"""
placement_utils.py: Utility functions for cell placement.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Set, Optional
import math
import pandas as pd
from scipy.spatial import cKDTree


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
        try:
            nb = int(getattr(row, "net_bit"))
        except (ValueError, TypeError):
            # Skip non-integer values like 'x', 'z', etc.
            continue
        x = float(getattr(row, "x_um"))
        y = float(getattr(row, "y_um"))
        fp.setdefault(nb, []).append((x, y))
    return fp


def nets_by_cell(gdf: pd.DataFrame) -> Dict[str, Set[int]]:
    """Build mapping of cell_name -> set of all net_bits connected to that cell."""
    res: Dict[str, Set[int]] = {}
    for cell, grp in gdf.groupby("cell_name"):  # type: ignore[arg-type]
        nets: Set[int] = set()
        for nb in grp["net_bit"].dropna():
            try:
                nets.add(int(nb))
            except (ValueError, TypeError):
                # Skip non-integer values like 'x', 'z', etc.
                continue
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
        # Filter out non-integer net_bit values (like 'x', 'z', etc.)
        in_n: Set[int] = set()
        in_nets = grp.loc[dir_l.loc[grp.index] == "input", "net_bit"].dropna()
        for nb in in_nets:
            try:
                in_n.add(int(nb))
            except (ValueError, TypeError):
                # Skip non-integer values like 'x', 'z', etc.
                continue
        
        out_n: Set[int] = set()
        out_nets = grp.loc[dir_l.loc[grp.index] == "output", "net_bit"].dropna()
        for nb in out_nets:
            try:
                out_n.add(int(nb))
            except (ValueError, TypeError):
                # Skip non-integer values like 'x', 'z', etc.
                continue
        
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


def nearest_site(target: Tuple[float, float], free_site_ids: List[int], sites_df: pd.DataFrame, site_tree: Optional[cKDTree] = None, index_to_site_id: Optional[Dict[int, int]] = None) -> Optional[int]:
    """Find nearest free site to target position.
    
    Uses Manhattan distance (L1) as primary, Euclidean (L2) as tie-breaker.
    Optimized using KD-tree for fast spatial queries.
    
    Args:
        target: (x, y) target position in microns
        free_site_ids: List of available site IDs
        sites_df: DataFrame with site information (columns: site_id, x_um, y_um)
        site_tree: Pre-built KD-tree of all sites (optional, for performance)
        index_to_site_id: Mapping from DataFrame index to site_id (optional, for performance)
    
    Returns:
        site_id of nearest free site, or None if no free sites
    """
    if not free_site_ids:
        return None
    
    tx, ty = target
    free_set = set(free_site_ids)
    
    # If we have a pre-built tree, use it for fast lookup
    if site_tree is not None and index_to_site_id is not None:
        # Query tree for nearest candidates (query more than we need to account for filtering)
        k = min(len(free_site_ids), 100)  # Query up to 100 nearest sites
        distances, indices = site_tree.query([tx, ty], k=k)
        
        # Handle single result (k=1 returns scalar, not array)
        if k == 1:
            distances = [distances]
            indices = [indices]
        
        # Find first free site in results
        best = None
        best_key = (float("inf"), float("inf"))
        for tree_idx in indices:
            # Convert tree index (DataFrame index) to site_id
            site_id = index_to_site_id.get(int(tree_idx))
            if site_id is not None and site_id in free_set:
                # Use DataFrame index (tree_idx) to access row, then get coordinates
                row = sites_df.iloc[int(tree_idx)]
                sx = float(row["x_um"])
                sy = float(row["y_um"])
                l1 = abs(tx - sx) + abs(ty - sy)
                l2 = math.hypot(tx - sx, ty - sy)
                key = (l1, l2)
                if key < best_key:
                    best_key = key
                    best = site_id
            if best is not None:
                break
        
        # If we found a free site in the k nearest, return it
        if best is not None:
            return best
    
    # Fallback: linear search through free sites (slower but always works)
    # This handles cases where tree isn't provided or k nearest don't include free sites
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


def build_site_tree(sites_df: pd.DataFrame) -> Tuple[cKDTree, Dict[int, int]]:
    """Build KD-tree and mapping for fast nearest site queries.
    
    Args:
        sites_df: DataFrame with site information (columns: site_id, x_um, y_um)
    
    Returns:
        (tree, index_to_site_id) where:
        - tree: cKDTree built from site coordinates
        - index_to_site_id: Dict mapping DataFrame index -> site_id
    """
    # Extract coordinates
    coords = sites_df[["x_um", "y_um"]].values
    
    # Build KD-tree
    tree = cKDTree(coords)
    
    # Build mapping from DataFrame index to site_id
    # Tree indices correspond to DataFrame row indices
    index_to_site_id = {}
    for idx, row in sites_df.iterrows():
        site_id = int(row["site_id"])
        index_to_site_id[int(idx)] = site_id
    
    return tree, index_to_site_id


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

