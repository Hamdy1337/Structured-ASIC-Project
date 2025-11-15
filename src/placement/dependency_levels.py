"""
dependency_levels.py: Module for building topological dependency levels for cells.
"""
from __future__ import annotations

from typing import Dict, Set, Any
import pandas as pd


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

