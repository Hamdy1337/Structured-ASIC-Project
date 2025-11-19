"""
placer.py: Module for assigning cells on the sASIC fabric.
    python -m src.placement.placer
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Any, Set
import math
import random
import re

import pandas as pd

from src.parsers.fabric_db import get_fabric_db, Fabric
from src.parsers.pins_parser import load_and_validate as load_pins_df
from src.parsers.netlist_parser import get_netlist_graph


def _parse_bus(name: str) -> Tuple[str, Optional[int]]:
    """Parse a signal/port name into (base, bit).

    Supports forms like "data[7]" and "data_7". If not a bus, returns (name, None).
    """
    s = str(name).strip()
    m = re.match(r"^(?P<base>.+)\[(?P<bit>\d+)\]$", s)
    if m:
        return m.group("base"), int(m.group("bit"))
    m2 = re.match(r"^(?P<base>.*?)[_](?P<bit>\d+)$", s)
    if m2:
        return m2.group("base"), int(m2.group("bit"))
    return s, None


def _normalize_side(side: Optional[str]) -> Optional[str]:
    """Normalize side strings to cardinal letters 'N','S','E','W'.

    Accepts: 'north','south','east','west', or single-letter variants (any case).
    Returns 'N','S','E','W' or None if unknown.
    """
    if not isinstance(side, str):
        return None
    s = side.strip().lower()
    mapping = {
        "n": "N", "north": "N",
        "s": "S", "south": "S",
        "e": "E", "east": "E",
        "w": "W", "west": "W",
    }
    return mapping.get(s)


def _side_rank(side: Optional[str]) -> int:
    order = {"W": 0, "S": 1, "E": 2, "N": 3}
    card = _normalize_side(side)
    return order[card] if card in order else 4


def _sort_key_for_pin(row: pd.Series) -> Tuple[int, float, float, int]:
    side = row.get("side")
    x = float(row.get("x_um", 0.0))
    y = float(row.get("y_um", 0.0))
    track = int(row.get("track_idx", 0) or 0)
    # Sort by side, then primary axis within the side, then secondary axis, then track
    sr = _side_rank(side)
    card = _normalize_side(side)
    if card in ("N", "S"):
        primary, secondary = x, y
    else:
        primary, secondary = y, x
    return (sr, primary, secondary, track)


def assign_ports_to_pins(pins_df: pd.DataFrame, ports_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Assign top-level ports to available I/O pins efficiently.

    Strategy:
    - Normalize directions to lower-case once.
    - Build per-direction candidate pin lists sorted physically (by side and coordinate).
    - Group ports by (direction, bus base) and assign in bit order.
    - Apply all updates in bulk.

    Returns:
        (updated_pins_df, assignments_df)
    """
    if pins_df.empty or ports_df.empty:
        return pins_df.copy(), pd.DataFrame(columns=[
            "pin_index", "pin_name", "port_name", "direction", "net_base", "net_bit"
        ])

    pins = pins_df.copy()
    pins["assigned"] = False
    pins["assigned_port"] = None
    pins["net_base"] = None
    pins["net_bit"] = None

    # Normalize directions
    pins_dir = pins["direction"].astype(str).str.lower()
    ports = ports_df.copy()
    ports_dir = ports["direction"].astype(str).str.lower()
    ports["_direction_l"] = ports_dir

    # Build candidate indices per direction and per (direction, base) from pin names
    candidates_by_dir: Dict[str, List[int]] = {}
    candidates_by_dir_base: Dict[str, Dict[str, List[int]]] = {}
    for dir_l, group in pins.groupby(pins_dir):  # type: ignore
        # Sort group physically for a stable mapping
        sort_index = group.apply(_sort_key_for_pin, axis=1)
        ordered_idx = list(group.loc[sort_index.sort_values().index].index)
        dir_key_local = str(dir_l)
        candidates_by_dir[dir_key_local] = ordered_idx
        # Build base-specific bins using pin 'name'
        base_bins: Dict[str, List[int]] = {}
        if "name" in pins.columns:
            for pin_idx in ordered_idx:
                pin_name = str(pins.at[pin_idx, "name"])  # type: ignore[index]
                pin_base, _ = _parse_bus(pin_name)
                base_bins.setdefault(pin_base, []).append(pin_idx)
        candidates_by_dir_base[dir_key_local] = base_bins

    # Use ports['net_name'] as the grouping base exactly (per-signal matching),
    # and ports['net_bit'] for ordering; fallback to port_name only if net_name missing.
    if "net_name" in ports.columns:
        ports["_bus_base"] = ports["net_name"].astype(str)
    else:
        # Fallback: derive base from port_name (still no bit parsing)
        if "port_name" in ports.columns:
            ports["_bus_base"] = ports["port_name"].astype(str)
        else:
            ports["_bus_base"] = pd.Series(ports.index.astype(str), index=ports.index)
    # Bit ordering comes from synthesized net_bit column
    ports["_bus_bit"] = ports["net_bit"] if "net_bit" in ports.columns else None

    assignments: List[Tuple[int, str, str, str, str, Optional[int]]] = []

    # Helper to remove a pin from pools (direction pool and base bin)
    def _remove_from_pools(dir_key_rm: str, base_key_rm: Optional[str], pin_idx_rm: int) -> None:
        pool_dir = candidates_by_dir.get(dir_key_rm, [])
        candidates_by_dir[dir_key_rm] = [i for i in pool_dir if i != pin_idx_rm]
        bins_rm = candidates_by_dir_base.get(dir_key_rm, {})
        if base_key_rm is None:
            # remove from all base bins under this direction
            for bkey in list(bins_rm.keys()):
                bins_rm[bkey] = [i for i in bins_rm[bkey] if i != pin_idx_rm]
        else:
            if base_key_rm in bins_rm:
                bins_rm[base_key_rm] = [i for i in bins_rm[base_key_rm] if i != pin_idx_rm]
        candidates_by_dir_base[dir_key_rm] = bins_rm

    # Consolidated assignment writer to avoid duplication
    def _commit_assignment(pin_idx: int, dir_key: str, base_key: str,
                           port_name_val: Optional[str], bit_val: Optional[int],
                           remove_all_bins: bool = False) -> None:
        pins.at[pin_idx, "assigned"] = True
        pins.at[pin_idx, "assigned_port"] = port_name_val
        pins.at[pin_idx, "net_base"] = base_key
        pins.at[pin_idx, "net_bit"] = bit_val
        pin_name_val = str(pins.at[pin_idx, "name"]) if "name" in pins.columns else str(pin_idx)
        assignments.append(
            (
                int(pin_idx),
                pin_name_val,
                str(port_name_val) if port_name_val is not None else "",
                dir_key,
                base_key,
                bit_val,
            )
        )
        _remove_from_pools(dir_key, None if remove_all_bins else base_key, pin_idx)

    # ------------------------------------------------------------------
    # Special handling: prioritize dedicated clock and reset pins
    # We look for ports named like clk/clock and rst/rst_n/reset/reset_n
    # and map them to pins with identical names first (case-insensitive).
    # This ensures stable, intention-aligned placement for global nets.
    # ------------------------------------------------------------------
    special_port_patterns = {
        "clock": re.compile(r"^(clk|clock)$", re.IGNORECASE),
        "reset": re.compile(r"^(rst|rst_n|reset|reset_n)$", re.IGNORECASE),
    }
    port_name_col = "port_name" if "port_name" in ports.columns else ("name" if "name" in ports.columns else None)
    used_port_indices: List[Any] = []
    if port_name_col:
        pin_name_col = "name" if "name" in pins.columns else None
        for pattern in special_port_patterns.values():
            role_ports = ports[ports[port_name_col].astype(str).str.match(pattern)]
            if role_ports.empty:
                continue
            if pin_name_col:
                candidate_pins = pins[(pins[pin_name_col].astype(str).str.match(pattern)) & (pins_dir == "input") & (~pins["assigned"])].copy()
            else:
                candidate_pins = pd.DataFrame()
            if candidate_pins.empty:
                continue
            sort_index_cp = candidate_pins.apply(_sort_key_for_pin, axis=1)
            ordered_pin_indices = list(candidate_pins.loc[sort_index_cp.sort_values().index].index)
            pin_cursor = 0
            for row in role_ports.itertuples(index=True):
                if pin_cursor >= len(ordered_pin_indices):
                    break
                pin_idx = ordered_pin_indices[pin_cursor]
                pin_cursor += 1
                # Set base directly from net_name if available, else use port name string as-is
                base_only = getattr(row, "net_name", getattr(row, port_name_col))
                original_bit = getattr(row, "net_bit", None)
                _commit_assignment(
                    pin_idx=pin_idx,
                    dir_key="input",
                    base_key=str(base_only),
                    port_name_val=str(getattr(row, port_name_col)),
                    bit_val=original_bit,
                    remove_all_bins=True,
                )
                used_port_indices.append(getattr(row, "Index"))
            # pools already updated per assignment via _commit_assignment

    # Exclude already assigned special ports from further grouping
    residual_ports = ports[~ports.index.isin(used_port_indices)].copy()  # type: ignore[arg-type]

    # Direction override for certain bases (e.g., oeb ports drive input pins)
    def _dir_for_port_base(dir_label: Any, base_label: Any) -> str:
        base_s = str(base_label).lower() if isinstance(base_label, str) else str(base_label)
        if base_s == "oeb":
            return "input"
        return str(dir_label)

    # Group ports by (direction, base)
    for (dir_l, base), g in residual_ports.groupby(["_direction_l", "_bus_base"], dropna=False):  # type: ignore
        # Retrieve candidate pool for this direction
        dir_key: str = _dir_for_port_base(dir_l, base)  # type: ignore[arg-type]
        base_key: str = str(base)  # type: ignore[arg-type]
        pool_dir = candidates_by_dir.get(dir_key, [])
        pool_base = candidates_by_dir_base.get(dir_key, {}).get(base_key, [])
        if not pool_dir and not pool_base:
            continue

        # Sort group by bit number if present, else stable order
        g_sorted = g.sort_values(by=["_bus_bit"], na_position="last", kind="stable")

        # Iterate and assign sequentially from the pool
        pool_pos_base = 0
        pool_pos_dir = 0
        for row in g_sorted.itertuples(index=False):
            # Prefer base-matching pins first, then fall back to direction-only
            if pool_pos_base < len(pool_base):
                pin_idx = pool_base[pool_pos_base]
                pool_pos_base += 1
            elif pool_pos_dir < len(pool_dir):
                pin_idx = pool_dir[pool_pos_dir]
                pool_pos_dir += 1
            else:
                break  # out of pins
            # Prefer 'port_name' column from ports_df
            port_name_val = getattr(row, "port_name", getattr(row, "name", None))
            # Use original net_bit from ports_df if present (Yosys net index)
            original_bit = getattr(row, "net_bit", None)
            # Commit and remove from pools to avoid reuse
            _commit_assignment(
                pin_idx=pin_idx,
                dir_key=dir_key,
                base_key=base_key,
                port_name_val=port_name_val,
                bit_val=original_bit,
                remove_all_bins=False,
            )
        # Refresh pools for next group
        pool_dir = candidates_by_dir.get(dir_key, [])
        pool_base = candidates_by_dir_base.get(dir_key, {}).get(base_key, [])

    assignments_df = pd.DataFrame(
        assignments,
        columns=["pin_index", "pin_name", "port_name", "direction", "net_base", "net_bit"],
    )
    return pins, assignments_df

def build_dependency_levels(
    updated_pins: pd.DataFrame,
    netlist_graph: pd.DataFrame
    ) -> pd.DataFrame:
    """
    Build dependency levels for cells based on net connectivity.

    Contract:
    - updated_pins: DataFrame with columns ['assigned', 'direction', 'net_bit'].
      Seeds known nets with assigned input pins' net_bit values.
    - netlist_graph: DataFrame with columns at least ['cell_name','direction','net_bit']
      where each row is a cell pin connection (input or output) to a logical net.

    Output:
    - Returns a copy of netlist_graph with a new integer column 'dependency_level'
      assigning a topological level to each row based on its cell's level.
    """
    g = netlist_graph.copy()
    required = {"cell_name", "direction", "net_bit"}
    if not required.issubset(g.columns):
        g["dependency_level"] = 0
        return g

    # Seed known nets from assigned input pins
    pins = updated_pins.copy()
    if "assigned" in pins.columns and "direction" in pins.columns and "net_bit" in pins.columns:
        seed_mask = (pins["assigned"].astype(bool)) & (pins["direction"].astype(str).str.lower() == "input") & pins["net_bit"].notna()
        known_nets: Set[int] = set(pins.loc[seed_mask, "net_bit"].dropna().astype(int).tolist())
    else:
        known_nets = set()

    # Group inputs/outputs per cell
    dir_lower = g["direction"].astype(str).str.lower()
    inputs_by_cell: Dict[Any, Set[int]] = {}
    outputs_by_cell: Dict[Any, Set[int]] = {}
    # Group by cell_name (ignore type analysis complaints with explicit loop)
    for cell, grp in g.groupby("cell_name"):  # type: ignore[arg-type]
        in_mask = dir_lower.loc[grp.index] == "input"
        out_mask = dir_lower.loc[grp.index] == "output"
        in_nets: Set[int] = set(grp.loc[in_mask, "net_bit"].dropna().astype(int).tolist())
        out_nets: Set[int] = set(grp.loc[out_mask, "net_bit"].dropna().astype(int).tolist())
        inputs_by_cell[cell] = in_nets
        outputs_by_cell[cell] = out_nets

    remaining: Set[Any] = set(inputs_by_cell.keys())
    cell_level: Dict[Any, int] = {}
    level = 0
    # Topological layering with cycle fallback
    while remaining:
        ready = {c for c in remaining if inputs_by_cell[c] <= known_nets}
        if not ready:
            # Cycle or unsatisfied net; assign all remaining same level and break
            for c in remaining:
                cell_level[c] = level
            break
        for c in ready:
            cell_level[c] = level
            known_nets |= outputs_by_cell.get(c, set())
        remaining -= ready
        level += 1

    # Map levels; fill missing with -1 explicitly via list comprehension to avoid fillna typing issues
    g["dependency_level"] = [int(cell_level[c]) if c in cell_level else -1 for c in g["cell_name"].tolist()]
    return g



def place_cells_greedy_sim_anneal(
    fabric: Fabric,
    fabric_df: pd.DataFrame,
    pins_df: pd.DataFrame,
    ports_df: pd.DataFrame,
    netlist_graph: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Place cells on the fabric using a greedy simulated annealing algorithm.

    Steps:
    1) Assign ports to pins (done above).
    2) Levelize cells from netlist.
    3) Build available site list from fabric.
    4) Greedy initial placement (median-of-drivers target, Manhattan nearest, L2 tie-breaker).
    5) Small-batch simulated annealing per level to reduce local HPWL.

    Returns updated pins (for backward compat). Placement summary printed in __main__.
    """
    updated_pins, _assign = assign_ports_to_pins(pins_df, ports_df)

    # ---- Helper builders ----
    def _build_sites(df: pd.DataFrame) -> pd.DataFrame:
        # Use cell_x/cell_y as site coordinates; drop duplicates
        cols = [c for c in ["cell_x", "cell_y", "tile_name", "cell_type"] if c in df.columns]
        sites = df[cols].drop_duplicates().reset_index(drop=True).copy()
        sites.rename(columns={"cell_x": "x_um", "cell_y": "y_um"}, inplace=True)
        sites.insert(0, "site_id", range(len(sites)))  # type: ignore[arg-type]
        return sites

    def _fixed_points_from_pins(pins: pd.DataFrame) -> Dict[int, List[Tuple[float, float]]]:
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

    def _nets_by_cell(gdf: pd.DataFrame) -> Dict[str, Set[int]]:
        res: Dict[str, Set[int]] = {}
        for cell, grp in gdf.groupby("cell_name"):  # type: ignore[arg-type]
            nets = set(grp["net_bit"].dropna().astype(int).tolist())
            res[str(cell)] = nets
        return res

    def _in_out_nets_by_cell(gdf: pd.DataFrame) -> Tuple[Dict[str, Set[int]], Dict[str, Set[int]]]:
        ins: Dict[str, Set[int]] = {}
        outs: Dict[str, Set[int]] = {}
        dir_l = gdf["direction"].astype(str).str.lower()
        for cell, grp in gdf.groupby("cell_name"):  # type: ignore[arg-type]
            in_n = set(grp.loc[dir_l.loc[grp.index] == "input", "net_bit"].dropna().astype(int).tolist())
            out_n = set(grp.loc[dir_l.loc[grp.index] == "output", "net_bit"].dropna().astype(int).tolist())
            ins[str(cell)] = in_n
            outs[str(cell)] = out_n
        return ins, outs

    def _median(vals: List[float]) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        n = len(s)
        mid = n // 2
        if n % 2 == 1:
            return s[mid]
        return 0.5 * (s[mid - 1] + s[mid])

    def _nearest_site(target: Tuple[float, float], free_site_ids: List[int], sites_df: pd.DataFrame) -> Optional[int]:
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

    def _hpwl_for_nets(nets: Set[int], pos_cells: Dict[str, Tuple[float, float]],
                        cell_to_nets: Dict[str, Set[int]], fixed_pts: Dict[int, List[Tuple[float, float]]]) -> float:
        total = 0.0
        for nb in nets:
            xs: List[float] = []
            ys: List[float] = []
            # Placed cells contributing to this net
            for cell, pos in pos_cells.items():
                if nb in cell_to_nets.get(cell, set()):
                    xs.append(pos[0]); ys.append(pos[1])
            # Fixed pins on this net
            for (fx, fy) in fixed_pts.get(nb, []):
                xs.append(fx); ys.append(fy)
            if len(xs) >= 2:
                total += (max(xs) - min(xs)) + (max(ys) - min(ys))
        return total

    def _anneal_batch(batch_cells: List[str], pos_cells: Dict[str, Tuple[float, float]],
                      assignments: Dict[str, int], sites_df: pd.DataFrame,
                      cell_nets: Dict[str, Set[int]], fixed_pts: Dict[int, List[Tuple[float, float]]],
                      iters: int = 200) -> None:
        if len(batch_cells) < 2:
            return
        # Precompute nets touched by the batch
        batch_nets: Set[int] = set()
        for c in batch_cells:
            batch_nets |= cell_nets.get(c, set())
        # Initial HPWL
        cur = _hpwl_for_nets(batch_nets, pos_cells, cell_nets, fixed_pts)
        # Temperature schedule
        T0 = max(1.0, cur / 50.0)
        temp = T0
        alpha = 0.90
        rng = random.Random(42)
        choose = batch_cells
        for i in range(iters):
            a, b = rng.sample(choose, 2)
            if a == b:
                continue
            sa = assignments[a]; sb = assignments[b]
            # nets affected
            nets_aff: Set[int] = set()
            nets_aff |= cell_nets.get(a, set())
            nets_aff |= cell_nets.get(b, set())
            # old
            old = _hpwl_for_nets(nets_aff, pos_cells, cell_nets, fixed_pts)
            # apply swap
            assignments[a], assignments[b] = sb, sa
            pos_cells[a] = (float(sites_df.at[sb, "x_um"]), float(sites_df.at[sb, "y_um"]))  # type: ignore[arg-type]
            pos_cells[b] = (float(sites_df.at[sa, "x_um"]), float(sites_df.at[sa, "y_um"]))  # type: ignore[arg-type]
            # new local
            new = _hpwl_for_nets(nets_aff, pos_cells, cell_nets, fixed_pts)
            d = new - old
            accept = d <= 0 or rng.random() < math.exp(-d / max(temp, 1e-6))
            if accept:
                cur += d
            else:
                # revert
                assignments[a], assignments[b] = sa, sb
                pos_cells[a] = (float(sites_df.at[sa, "x_um"]), float(sites_df.at[sa, "y_um"]))  # type: ignore[arg-type]
                pos_cells[b] = (float(sites_df.at[sb, "x_um"]), float(sites_df.at[sb, "y_um"]))  # type: ignore[arg-type]
            if (i + 1) % 20 == 0:
                temp *= alpha

    # ---- Build inputs for placement ----
    sites_df = _build_sites(fabric_df)
    free_site_ids: List[int] = sites_df["site_id"].tolist()
    fixed_pts = _fixed_points_from_pins(updated_pins)
    g_levels = build_dependency_levels(updated_pins, netlist_graph)
    ins_by_cell, outs_by_cell = _in_out_nets_by_cell(g_levels)
    nets_by_cell = _nets_by_cell(g_levels)

    # Order cells by level
    cell_levels = g_levels[["cell_name", "dependency_level"]].drop_duplicates()
    order = cell_levels.sort_values(by=["dependency_level", "cell_name"]).reset_index(drop=True)

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

    # Level-by-level greedy + SA in small batches
    batch_size = 24
    for lvl in sorted(order["dependency_level"].unique()):
        # Build list of cell names (already strings or convertible)
        cells_series: pd.Series = order.loc[order["dependency_level"] == lvl, "cell_name"]  # type: ignore[assignment]
        level_cells = [str(x) for x in cells_series.tolist()]
        # Greedy initial placement for level
        for c in level_cells:
            # Compute target (median of driver points)
            pts = _driver_points(c)
            if pts:
                tx = _median([p[0] for p in pts]); ty = _median([p[1] for p in pts])
            else:
                # fallback: center of available sites
                tx = float(sites_df["x_um"].median()); ty = float(sites_df["y_um"].median())
            sid = _nearest_site((tx, ty), free_site_ids, sites_df)
            if sid is None:
                continue
            assignments[c] = sid
            pos_cells[c] = (float(sites_df.at[sid, "x_um"]), float(sites_df.at[sid, "y_um"]))  # type: ignore[arg-type]
            # consume site
            free_site_ids.remove(sid)
        # Small-batch SA within the level
        for i in range(0, len(level_cells), batch_size):
            batch: List[str] = [c for c in level_cells[i:i + batch_size] if c in assignments]
            _anneal_batch(batch, pos_cells, assignments, sites_df, nets_by_cell, fixed_pts, iters=200)

    # Save placement preview (optional): attach to fabric_df? We'll print in __main__
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
    netlist_file_path = "inputs/designs/aes_128_mapped.json"

    fabric, fabric_df = get_fabric_db(fabric_file_path, fabric_cells_file_path)
    pins_df, pins_meta = load_pins_df(pins_file_path)
    ports_df, netlist_graph = get_netlist_graph(netlist_file_path)

    print("\nNetlist Graph Info:")
    print(netlist_graph)

    print("\nPorts DataFrame:")
    print(ports_df)
    assigned_pins, placement_df = place_cells_greedy_sim_anneal(
        fabric=fabric,
        fabric_df=fabric_df,
        pins_df=pins_df,
        ports_df=ports_df,
        netlist_graph=netlist_graph,
    )

    print("\nAssigned Pins (preview):")
    cols = [c for c in ["name", "direction", "side", "x_um", "y_um", "assigned", "assigned_port", "net_base", "net_bit"] if c in assigned_pins.columns]
    print(assigned_pins[cols].head(30))
    print("\nPlacement (preview):")
    print(placement_df.head(20))