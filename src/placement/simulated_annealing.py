"""
simulated_annealing.py: Simulated annealing algorithm for placement optimization.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Set, Optional
import math
import random

from src.placement.placement_utils import hpwl_for_nets


def anneal_batch(
    batch_cells: List[str],
    pos_cells: Dict[str, Tuple[float, float]],
    assignments: Dict[str, int],
    sites_df,
    cell_nets: Dict[str, Set[int]],
    fixed_pts: Dict[int, List[Tuple[float, float]]],
    iters: int = 200,
    alpha: float = 0.90,
    T_initial: Optional[float] = None,
    seed: int = 42
) -> None:
    """Perform simulated annealing on a batch of cells.
    
    Args:
        batch_cells: List of cell names to optimize
        pos_cells: Dict mapping cell_name -> (x, y) position (modified in-place)
        assignments: Dict mapping cell_name -> site_id (modified in-place)
        sites_df: DataFrame with site information (columns: site_id, x_um, y_um)
        cell_nets: Dict mapping cell_name -> set of net_bits
        fixed_pts: Dict mapping net_bit -> list of (x, y) fixed pin positions
        iters: Number of SA iterations (moves per temperature step)
        alpha: Cooling rate (temperature multiplier per cooling step)
        T_initial: Initial temperature. If None, auto-calculates from initial HPWL
        seed: Random seed for reproducibility
    """
    if len(batch_cells) < 2:
        return
    
    # Precompute nets touched by the batch
    batch_nets: Set[int] = set()
    for c in batch_cells:
        batch_nets |= cell_nets.get(c, set())
    
    # Initial HPWL
    cur = hpwl_for_nets(batch_nets, pos_cells, cell_nets, fixed_pts)
    
    # Temperature schedule
    if T_initial is not None:
        T0 = T_initial
    else:
        # Auto-calculate: use initial HPWL as basis
        T0 = max(1.0, cur / 50.0)
    temp = T0
    rng = random.Random(seed)
    choose = batch_cells
    
    for i in range(iters):
        a, b = rng.sample(choose, 2)
        if a == b:
            continue
        
        sa = assignments[a]
        sb = assignments[b]
        
        # Nets affected by swap
        nets_aff: Set[int] = set()
        nets_aff |= cell_nets.get(a, set())
        nets_aff |= cell_nets.get(b, set())
        
        # Calculate HPWL before swap
        old = hpwl_for_nets(nets_aff, pos_cells, cell_nets, fixed_pts)
        
        # Apply swap
        assignments[a], assignments[b] = sb, sa
        pos_cells[a] = (float(sites_df.at[sb, "x_um"]), float(sites_df.at[sb, "y_um"]))  # type: ignore[arg-type]
        pos_cells[b] = (float(sites_df.at[sa, "x_um"]), float(sites_df.at[sa, "y_um"]))  # type: ignore[arg-type]
        
        # Calculate HPWL after swap
        new = hpwl_for_nets(nets_aff, pos_cells, cell_nets, fixed_pts)
        d = new - old
        
        # Accept or reject
        accept = d <= 0 or rng.random() < math.exp(-d / max(temp, 1e-6))
        if accept:
            cur += d
        else:
            # Revert swap
            assignments[a], assignments[b] = sa, sb
            pos_cells[a] = (float(sites_df.at[sa, "x_um"]), float(sites_df.at[sa, "y_um"]))  # type: ignore[arg-type]
            pos_cells[b] = (float(sites_df.at[sb, "x_um"]), float(sites_df.at[sb, "y_um"]))  # type: ignore[arg-type]
        
        # Cool down every 20 iterations
        if (i + 1) % 20 == 0:
            temp *= alpha

