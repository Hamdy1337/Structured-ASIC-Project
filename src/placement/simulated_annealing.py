"""
simulated_annealing.py: Simulated annealing algorithm for placement optimization.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Set, Optional
import math
import random
import pandas as pd
import numpy as np


def _hpwl_for_nets_optimized(
    nets: Set[int],
    pos_cells: Dict[str, Tuple[float, float]],
    net_to_cells: Dict[int, List[str]],  # Precomputed reverse mapping
    fixed_pts: Dict[int, List[Tuple[float, float]]]
) -> float:
    """Optimized HPWL calculation using NumPy vectorization.
    
    Args:
        nets: Set of net bits to calculate HPWL for
        pos_cells: Dict mapping cell_name -> (x, y) position
        net_to_cells: Precomputed dict mapping net_bit -> list of cell names on that net
        fixed_pts: Dict mapping net_bit -> list of (x, y) fixed pin positions
    """
    total = 0.0
    for nb in nets:
        xs: List[float] = []
        ys: List[float] = []
        
        # Get cells on this net (using precomputed mapping)
        for cell in net_to_cells.get(nb, []):
            pos = pos_cells.get(cell)
            if pos is not None:
                xs.append(pos[0])
                ys.append(pos[1])
        
        # Fixed pins on this net
        for (fx, fy) in fixed_pts.get(nb, []):
            xs.append(fx)
            ys.append(fy)
        
        if len(xs) >= 2:
            # Use NumPy for faster min/max on larger lists
            if len(xs) > 10:
                xs_arr = np.array(xs, dtype=np.float64)
                ys_arr = np.array(ys, dtype=np.float64)
                total += float(np.max(xs_arr) - np.min(xs_arr) + np.max(ys_arr) - np.min(ys_arr))
            else:
                total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    
    return total


def _pick_refine_move_optimized(
    batch_cells: List[str],
    cell_pos_x: np.ndarray,
    cell_pos_y: np.ndarray,
    cell_to_idx: Dict[str, int],
    max_distance: float,
    rng: random.Random,
    max_attempts: int = 50
) -> Optional[Tuple[str, str]]:
    """Optimized refine move picking using NumPy vectorization."""
    if len(batch_cells) < 2:
        return None
    
    batch_indices = np.array([cell_to_idx[c] for c in batch_cells], dtype=np.int32)
    n_batch = len(batch_indices)
    
    for _ in range(max_attempts):
        # Random selection using indices
        idx_a, idx_b = rng.sample(range(n_batch), 2)
        if idx_a == idx_b:
            continue
        
        i_a = batch_indices[idx_a]
        i_b = batch_indices[idx_b]
        
        # Vectorized Manhattan distance
        dist = float(np.abs(cell_pos_x[i_a] - cell_pos_x[i_b]) + 
                     np.abs(cell_pos_y[i_a] - cell_pos_y[i_b]))
        
        if dist <= max_distance:
            return (batch_cells[idx_a], batch_cells[idx_b])
    
    # Fallback: return any two cells if no nearby pair found
    if len(batch_cells) >= 2:
        idx_a, idx_b = rng.sample(range(n_batch), 2)
        return (batch_cells[idx_a], batch_cells[idx_b])
    return None


def _pick_explore_move_optimized(
    batch_cells: List[str],
    cell_pos_x: np.ndarray,
    cell_pos_y: np.ndarray,
    cell_to_idx: Dict[str, int],
    window_size: float,
    rng: random.Random,
    max_attempts: int = 50
) -> Optional[Tuple[str, str]]:
    """Optimized explore move picking using NumPy vectorization."""
    if len(batch_cells) < 2:
        return None
    
    batch_indices = np.array([cell_to_idx[c] for c in batch_cells], dtype=np.int32)
    n_batch = len(batch_indices)
    
    for _ in range(max_attempts):
        idx_a, idx_b = rng.sample(range(n_batch), 2)
        if idx_a == idx_b:
            continue
        
        i_a = batch_indices[idx_a]
        i_b = batch_indices[idx_b]
        
        # Vectorized Manhattan distance
        dist = float(np.abs(cell_pos_x[i_a] - cell_pos_x[i_b]) + 
                     np.abs(cell_pos_y[i_a] - cell_pos_y[i_b]))
        
        if dist <= window_size:
            return (batch_cells[idx_a], batch_cells[idx_b])
    
    # Fallback: return any two cells if no pair found within window
    if len(batch_cells) >= 2:
        idx_a, idx_b = rng.sample(range(n_batch), 2)
        return (batch_cells[idx_a], batch_cells[idx_b])
    return None


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
    W_initial: float = 0.5,
    seed: int = 42,
    cell_types: Optional[Dict[str, Optional[str]]] = None
) -> None:
    """Perform simulated annealing on a batch of cells with hybrid move set.
    
    OPTIMIZED VERSION: Uses NumPy arrays for fast lookups and vectorized operations.
    
    Args:
        batch_cells: List of cell names to optimize
        pos_cells: Dict mapping cell_name -> (x, y) position (modified in-place)
        assignments: Dict mapping cell_name -> site_id (modified in-place)
        sites_df: DataFrame with site information (columns: site_id, x_um, y_um, cell_type)
        cell_nets: Dict mapping cell_name -> set of net_bits
        fixed_pts: Dict mapping net_bit -> list of (x, y) fixed pin positions
        iters: Number of SA iterations (moves per temperature step)
        alpha: Cooling rate (temperature multiplier per cooling step). Also used for window cooling.
        T_initial: Initial temperature. If None, auto-calculates from initial HPWL
        p_refine: Probability of choosing a refine move (default: 0.7)
        p_explore: Probability of choosing an explore move (default: 0.3)
        refine_max_distance: Maximum Manhattan distance for refine moves in microns (default: 100.0)
        W_initial: Initial exploration window size as fraction of die size (default: 0.5 = 50%)
        seed: Random seed for reproducibility
        cell_types: Optional dict mapping cell_name -> cell_type for compatibility checking
    """
    if len(batch_cells) < 2:
        return
    
    # ===== OPTIMIZATION 1: Precompute NumPy arrays for site lookups =====
    # Convert sites_df to NumPy arrays for O(1) access instead of O(log n) DataFrame.at[]
    site_x_arr = sites_df["x_um"].to_numpy(dtype=np.float64)
    site_y_arr = sites_df["y_um"].to_numpy(dtype=np.float64)
    
    # Precompute site type array if available
    if "cell_type" in sites_df.columns and cell_types is not None:
        site_type_arr = sites_df["cell_type"].astype(str).to_numpy()
    else:
        site_type_arr = None
    
    # Build mapping from cell name to index in batch
    cell_to_idx: Dict[str, int] = {cell: i for i, cell in enumerate(batch_cells)}
    
    # Precompute cell positions as NumPy arrays for vectorized distance calculations
    cell_pos_x = np.array([pos_cells.get(c, (0.0, 0.0))[0] for c in batch_cells], dtype=np.float64)
    cell_pos_y = np.array([pos_cells.get(c, (0.0, 0.0))[1] for c in batch_cells], dtype=np.float64)
    
    # Compatibility check helper (optimized to use NumPy array)
    def _is_compatible(cell: str, site_id: int) -> bool:
        if cell_types is None or site_type_arr is None:
            return True
        req = cell_types.get(cell)
        if req is None:
            return True
        try:
            st = site_type_arr[site_id]
            if pd.isna(st) or st == 'nan':
                return True
            return str(st) == str(req)
        except (IndexError, KeyError):
            return True
    
    # Precompute nets touched by the batch
    batch_nets: Set[int] = set()
    for c in batch_cells:
        batch_nets |= cell_nets.get(c, set())
    
    # Build net_to_cells mapping for ALL nets (needed for correct HPWL calculation)
    # This includes all cells on each net, not just batch cells
    net_to_cells: Dict[int, List[str]] = {}
    for cell in pos_cells.keys():  # Iterate over ALL placed cells
        for net in cell_nets.get(cell, set()):
            net_to_cells.setdefault(net, []).append(cell)
    
    # Initial HPWL 
    cur = _hpwl_for_nets_optimized(batch_nets, pos_cells, net_to_cells, fixed_pts)
    start_hpwl = cur
    
    # Temperature schedule
    if T_initial is not None:
        T0 = T_initial
    else:
        # Auto-calculate T0 based on initial HPWL
        # We want initial acceptance probability of bad moves to be low for refinement
        # If typical delta is ~1% of HPWL, say delta = cur * 0.01
        # We want exp(-delta/T) to be small, e.g. 0.1
        # -delta/T = ln(0.1) ~ -2.3 => T = delta/2.3 ~ 0.004 * cur
        # Let's use T0 = cur / 500.0 (0.2%)
        T0 = max(0.1, cur / 500.0)
        print(f"      [SA] Start Batch: Cells={len(batch_cells)} T0={T0:.3f} HPWL={cur:.1f}")

    temp = T0
    rng = random.Random(seed)
    
    # Exploration window schedule (tied to alpha)
    die_width = float(sites_df["x_um"].max())
    die_height = float(sites_df["y_um"].max())
    die_size = max(die_width, die_height)
    W0 = W_initial * die_size
    window_size = W0
    
    # Normalize probabilities
    total_prob = p_refine + p_explore
    if total_prob > 0:
        p_refine_norm = p_refine / total_prob
    else:
        p_refine_norm = 1.0
    
    accepted_moves = 0
    
    for i in range(iters):
        # Choose move type based on probability
        move_type_rand = rng.random()
        if move_type_rand < p_refine_norm:
            # Refine move: swap nearby cells
            move_result = _pick_refine_move_optimized(
                batch_cells, cell_pos_x, cell_pos_y, cell_to_idx, 
                refine_max_distance, rng
            )
        else:
            # Explore move: swap cells within current window (using optimized version)
            move_result = _pick_explore_move_optimized(
                batch_cells, cell_pos_x, cell_pos_y, cell_to_idx, 
                window_size, rng
            )
        
        if move_result is None:
            continue
        
        a, b = move_result
        if a == b:
            continue
        
        sa = assignments[a]
        sb = assignments[b]
        
        # Enforce site-type compatibility on proposed swap
        if not (_is_compatible(a, sb) and _is_compatible(b, sa)):
            continue
        
        # Nets affected by swap
        nets_aff: Set[int] = set()
        nets_aff |= cell_nets.get(a, set())
        nets_aff |= cell_nets.get(b, set())
        
        # Calculate HPWL before swap (using optimized version)
        old = _hpwl_for_nets_optimized(nets_aff, pos_cells, net_to_cells, fixed_pts)
        
        # Apply swap (using NumPy array lookups - O(1) instead of O(log n))
        assignments[a], assignments[b] = sb, sa
        new_x_a = float(site_x_arr[sb])
        new_y_a = float(site_y_arr[sb])
        new_x_b = float(site_x_arr[sa])
        new_y_b = float(site_y_arr[sa])
        
        pos_cells[a] = (new_x_a, new_y_a)
        pos_cells[b] = (new_x_b, new_y_b)
        
        # Update NumPy arrays for move picking (for next iteration)
        idx_a = cell_to_idx.get(a)
        idx_b = cell_to_idx.get(b)
        if idx_a is not None:
            cell_pos_x[idx_a] = new_x_a
            cell_pos_y[idx_a] = new_y_a
        if idx_b is not None:
            cell_pos_x[idx_b] = new_x_b
            cell_pos_y[idx_b] = new_y_b
        
        # Calculate HPWL after swap (using optimized version)
        new = _hpwl_for_nets_optimized(nets_aff, pos_cells, net_to_cells, fixed_pts)
        d = new - old
        
        # Accept or reject
        accept = d <= 0 or rng.random() < math.exp(-d / max(temp, 1e-6))
        if accept:
            cur += d
            accepted_moves += 1
        else:
            # Revert swap (using NumPy array lookups)
            assignments[a], assignments[b] = sa, sb
            old_x_a = float(site_x_arr[sa])
            old_y_a = float(site_y_arr[sa])
            old_x_b = float(site_x_arr[sb])
            old_y_b = float(site_y_arr[sb])
            
            pos_cells[a] = (old_x_a, old_y_a)
            pos_cells[b] = (old_x_b, old_y_b)
            
            # Update NumPy arrays
            if idx_a is not None:
                cell_pos_x[idx_a] = old_x_a
                cell_pos_y[idx_a] = old_y_a
            if idx_b is not None:
                cell_pos_x[idx_b] = old_x_b
                cell_pos_y[idx_b] = old_y_b
        
        # Cool down every 20 iterations (temperature and window shrink together)
        if (i + 1) % 20 == 0:
            temp *= alpha
            window_size *= alpha
            
        if (i + 1) % 200 == 0:
            print(f"        [SA] Iter {i+1}: T={temp:.3f} HPWL={cur:.1f} Acc={accepted_moves/(i+1):.1%}")

    print(f"      [SA] End Batch: {start_hpwl:.1f} -> {cur:.1f} ({cur-start_hpwl:+.1f})")

