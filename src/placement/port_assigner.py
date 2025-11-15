"""
port_assigner.py: Module for assigning top-level ports to I/O pins.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Any
import re

import pandas as pd


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
    """Return numeric rank for side ordering: W=0, S=1, E=2, N=3, unknown=4."""
    order = {"W": 0, "S": 1, "E": 2, "N": 3}
    card = _normalize_side(side)
    return order[card] if card in order else 4


def _sort_key_for_pin(row: pd.Series) -> Tuple[int, float, float, int]:
    """Generate sort key for physical pin ordering."""
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

