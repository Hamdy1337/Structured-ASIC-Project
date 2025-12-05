"""
placement_utils.py: Utility functions for cell placement.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Set, Optional
import math
import pandas as pd
import numpy as np


def build_sites(df: pd.DataFrame) -> pd.DataFrame:
    # Use cell_x/cell_y as site coordinates; drop duplicates
    cols = [c for c in ["cell_x", "cell_y", "tile_name", "cell_type"] if c in df.columns]
    sites = df[cols].drop_duplicates().reset_index(drop=True).copy()
    sites.rename(columns={"cell_x": "x_um", "cell_y": "y_um"}, inplace=True)
    sites.insert(0, "site_id", range(len(sites)))  # type: ignore[arg-type]
    return sites


def fixed_points_from_pins(pins: pd.DataFrame) -> Dict[int, List[Tuple[float, float]]]:
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
    res: Dict[str, Set[int]] = {}
    for cell, grp in gdf.groupby("cell_name"):  # type: ignore[arg-type]
        nets = set(grp["net_bit"].dropna().astype(int).tolist())
        res[str(cell)] = nets
    return res


def in_out_nets_by_cell(gdf: pd.DataFrame) -> Tuple[Dict[str, Set[int]], Dict[str, Set[int]]]:
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
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def nearest_site(
    target: Tuple[float, float],
    is_free: np.ndarray,
    sites_df: pd.DataFrame,
    site_x: np.ndarray,
    site_y: np.ndarray,
    site_type_arr: Optional[np.ndarray],
    minx: float,
    miny: float,
    cell_w: float,
    cell_h: float,
    gx: int,
    gy: int,
    bins: List[List[List[int]]],
    required_type: Optional[str] = None
) -> Optional[int]:
    """Find nearest free site to target position using grid-based spatial index.
    
    Uses Manhattan distance (L1) as primary, Euclidean (L2) as tie-breaker.
    """
    tx, ty = target
    n_sites = int(len(sites_df))
    if n_sites == 0:
        return None
    bxi = int(np.clip(int((tx - minx) / max(cell_w, 1e-9)), 0, gx - 1))
    byi = int(np.clip(int((ty - miny) / max(cell_h, 1e-9)), 0, gy - 1))
    
    def _eval(cands: List[int]) -> Optional[int]:
        if not cands:
            return None
        arr = np.array(cands, dtype=int)
        mask = is_free[arr]
        if not mask.any():
            return None
        arr = arr[mask]
        if required_type is not None and site_type_arr is not None:
            tmask = (site_type_arr[arr] == str(required_type))
            if not tmask.any():
                return None
            arr = arr[tmask]
            if arr.size == 0:
                return None
        dx = np.abs(site_x[arr] - tx)
        dy = np.abs(site_y[arr] - ty)
        l1 = dx + dy
        l2 = np.hypot(dx, dy)
        order = np.lexsort((l2, l1))
        return int(arr[order[0]])
    
    max_r = max(gx, gy)
    for r in range(max_r):
        x0 = max(0, bxi - r)
        x1 = min(gx - 1, bxi + r)
        y0 = max(0, byi - r)
        y1 = min(gy - 1, byi + r)
        cands: List[int] = []
        for xi in range(x0, x1 + 1):
            for yi in range(y0, y1 + 1):
                cands.extend(bins[xi][yi])
        sid = _eval(cands)
        if sid is not None:
            return sid
    
    free_idxs = np.flatnonzero(is_free)
    if free_idxs.size == 0:
        return None
    if required_type is not None and site_type_arr is not None:
        tmask = (site_type_arr[free_idxs] == str(required_type))
        free_idxs = free_idxs[tmask]
    if free_idxs.size == 0:
        return None
    dx = np.abs(site_x[free_idxs] - tx)
    dy = np.abs(site_y[free_idxs] - ty)
    l1 = dx + dy
    l2 = np.hypot(dx, dy)
    order = np.lexsort((l2, l1))
    return int(free_idxs[order[0]])


def build_spatial_index(sites_df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Optional[np.ndarray], float, float, float, float, int, int, List[List[List[int]]]]:
    """Build grid-based spatial index for fast nearest site queries.
    
    Returns:
        (site_x, site_y, is_free, site_type_arr, minx, miny, cell_w, cell_h, gx, gy, bins)
    """
    site_x = sites_df["x_um"].to_numpy(dtype=float)
    site_y = sites_df["y_um"].to_numpy(dtype=float)
    n_sites = int(len(sites_df))
    is_free = np.ones(n_sites, dtype=bool)
    site_type_arr = sites_df["cell_type"].astype(str).to_numpy() if "cell_type" in sites_df.columns else None
    
    if n_sites > 0:
        minx = float(site_x.min())
        maxx = float(site_x.max())
        miny = float(site_y.min())
        maxy = float(site_y.max())
    else:
        minx = maxx = miny = maxy = 0.0
    
    spanx = max(maxx - minx, 1e-6)
    spany = max(maxy - miny, 1e-6)
    target_bins_axis = max(16, min(128, int(math.sqrt(n_sites / 100.0)) if n_sites > 0 else 16))
    gx = max(4, target_bins_axis)
    gy = max(4, target_bins_axis)
    cell_w = spanx / gx
    cell_h = spany / gy
    
    if n_sites > 0:
        bx = np.clip(((site_x - minx) / max(cell_w, 1e-9)).astype(int), 0, gx - 1)
        by = np.clip(((site_y - miny) / max(cell_h, 1e-9)).astype(int), 0, gy - 1)
    else:
        bx = np.array([], dtype=int)
        by = np.array([], dtype=int)
    
    bins: List[List[List[int]]] = [[[] for _ in range(gy)] for _ in range(gx)]
    for idx in range(n_sites):
        bins[int(bx[idx])][int(by[idx])].append(idx)
    
    return site_x, site_y, is_free, site_type_arr, minx, miny, cell_w, cell_h, gx, gy, bins


def hpwl_for_nets(
    nets: Set[int],
    pos_cells: Dict[str, Tuple[float, float]],
    cell_to_nets: Dict[str, Set[int]],
    fixed_pts: Dict[int, List[Tuple[float, float]]],
    net_to_cells: Optional[Dict[int, List[str]]] = None
) -> float:
    total = 0.0
    
    # If net_to_cells is not provided, we must iterate all cells (slow)
    # or build a temporary map. Building it once is O(N_cells), 
    # iterating all cells for each net is O(N_nets * N_cells).
    # Since N_nets ~ N_cells, building it is better.
    if net_to_cells is None:
        net_to_cells = {}
        for cell, cell_nets_set in cell_to_nets.items():
            if cell in pos_cells:
                for net in cell_nets_set:
                    net_to_cells.setdefault(net, []).append(cell)
    
    for nb in nets:
        xs: List[float] = []
        ys: List[float] = []
        
        # Placed cells contributing to this net
        # Use the map!
        if net_to_cells:
            for cell in net_to_cells.get(nb, []):
                if cell in pos_cells:
                    pos = pos_cells[cell]
                    xs.append(pos[0])
                    ys.append(pos[1])
        else:
            # Fallback (shouldn't be reached if we built the map above)
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

