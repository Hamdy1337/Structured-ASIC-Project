"""
simulated_annealing.py: Simulated annealing algorithm for placement optimization.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Set, Optional
import math
import random

from src.placement.placement_utils import hpwl_for_nets


def _manhattan_distance(pos1: Tuple[float, float], pos2: Tuple[float, float]) -> float:
    """Calculate Manhattan distance between two positions."""
    return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1])


def _pick_refine_move(
    batch_cells: List[str],
    pos_cells: Dict[str, Tuple[float, float]],
    max_distance: float,
    rng: random.Random,
    max_attempts: int = 50
) -> Optional[Tuple[str, str]]:
    """Pick two cells for a refine move (nearby cells).
    
    Returns two cells within max_distance of each other, or None if not found.
    """
    if len(batch_cells) < 2:
        return None
    
    for _ in range(max_attempts):
        a, b = rng.sample(batch_cells, 2)
        if a == b:
            continue
        
        pos_a = pos_cells.get(a)
        pos_b = pos_cells.get(b)
        if pos_a is None or pos_b is None:
            continue
        
        dist = _manhattan_distance(pos_a, pos_b)
        if dist <= max_distance:
            return (a, b)
    
    # Fallback: return any two cells if no nearby pair found
    if len(batch_cells) >= 2:
        return tuple(rng.sample(batch_cells, 2))  # type: ignore
    return None


def _pick_explore_move(
    batch_cells: List[str],
    rng: random.Random
) -> Optional[Tuple[str, str]]:
    """Pick two cells for an explore move (any two cells, typically far apart).
    
    For now, this is just a random selection. Later, we'll add window constraints.
    """
    if len(batch_cells) < 2:
        return None
    
    a, b = rng.sample(batch_cells, 2)
    if a == b:
        return None
    return (a, b)


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
    p_refine: float = 0.7,
    p_explore: float = 0.3,
    refine_max_distance: float = 100.0,
    seed: int = 42
) -> None:
    """Perform simulated annealing on a batch of cells with hybrid move set.
    
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
        p_refine: Probability of choosing a refine move (default: 0.7)
        p_explore: Probability of choosing an explore move (default: 0.3)
        refine_max_distance: Maximum Manhattan distance for refine moves in microns (default: 100.0)
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
    
    # Normalize probabilities
    total_prob = p_refine + p_explore
    if total_prob > 0:
        p_refine_norm = p_refine / total_prob
        p_explore_norm = p_explore / total_prob
    else:
        # Default to refine if both are 0
        p_refine_norm = 1.0
        p_explore_norm = 0.0
    
    for i in range(iters):
        # Choose move type based on probability
        move_type_rand = rng.random()
        if move_type_rand < p_refine_norm:
            # Refine move: swap nearby cells
            move_result = _pick_refine_move(batch_cells, pos_cells, refine_max_distance, rng)
        else:
            # Explore move: swap any cells (typically far apart)
            move_result = _pick_explore_move(batch_cells, rng)
        
        if move_result is None:
            continue
        
        a, b = move_result
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

