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
    p_refine: float = 0.6,
    p_explore: float = 0.2,
    p_relocate: float = 0.2,  # NEW: Probability of relocation to free site
    refine_max_distance: float = 100.0,
    W_initial: float = 0.5,
    seed: int = 42,
    cell_types: Optional[Dict[str, Optional[str]]] = None,
    net_to_cells: Optional[Dict[int, List[str]]] = None,
    frame_callback: Optional[callable] = None,  # Animation callback: fn(iteration, hpwl, temp, relocations)
    frame_interval: int = 50,  # Capture frame every N iterations
) -> Tuple[float, int]:
    """Perform simulated annealing on a batch of cells with hybrid move set.
    
    OPTIMIZED VERSION: Uses NumPy arrays for fast lookups and vectorized operations.
    
    Move Types:
        - Refine: Swap two nearby cells (within refine_max_distance)
        - Explore: Swap two cells within exploration window
        - Relocate: Move a cell from dense area to a FREE site in less dense area
    
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
        p_refine: Probability of choosing a refine move (default: 0.6)
        p_explore: Probability of choosing an explore move (default: 0.2)
        p_relocate: Probability of choosing a relocate move (default: 0.2) - moves cell to free site
        refine_max_distance: Maximum Manhattan distance for refine moves in microns (default: 100.0)
        W_initial: Initial exploration window size as fraction of die size (default: 0.5 = 50%)
        seed: Random seed for reproducibility
        cell_types: Optional dict mapping cell_name -> cell_type for compatibility checking
        net_to_cells: Optional precomputed dict mapping net_bit -> list of cell names. 
                      If None, will be computed from pos_cells (slow).
    """
    if len(batch_cells) < 2:
        return (0.0, 0)
    
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
    if net_to_cells is None:
        net_to_cells = {}
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
    
    # Track free sites for relocation moves
    assigned_sites = set(assignments.values())
    all_site_ids = set(range(len(site_x_arr)))
    free_sites = list(all_site_ids - assigned_sites)
    
    # Precompute density bins for efficient relocation target selection
    density_grid_size = 10  # 10x10 grid over die
    bin_width = die_width / density_grid_size
    bin_height = die_height / density_grid_size
    
    def _get_density_bin(x: float, y: float) -> Tuple[int, int]:
        bx = min(int(x / bin_width), density_grid_size - 1)
        by = min(int(y / bin_height), density_grid_size - 1)
        return (bx, by)
    
    def _pick_relocate_move(rng: random.Random) -> Optional[Tuple[str, int]]:
        """Pick a cell from dense area and a free site from less dense area."""
        if not free_sites:
            return None
        
        # Pick a random cell from the batch
        cell = rng.choice(batch_cells)
        cell_type = cell_types.get(cell) if cell_types else None
        
        # Find compatible free sites in less dense areas
        # Get current cell position and density
        cx, cy = pos_cells.get(cell, (die_width/2, die_height/2))
        
        # Find sites far from current position (spreading)
        # and away from dense center of placement
        centroid_x = sum(p[0] for p in pos_cells.values()) / max(1, len(pos_cells))
        centroid_y = sum(p[1] for p in pos_cells.values()) / max(1, len(pos_cells))
        
        # Score free sites by distance from centroid (prefer far from center)
        best_site = None
        best_score = -float('inf')
        
        # Sample up to 50 free sites for efficiency
        sample_sites = rng.sample(free_sites, min(50, len(free_sites)))
        for sid in sample_sites:
            sx, sy = float(site_x_arr[sid]), float(site_y_arr[sid])
            
            # Check type compatibility
            if cell_type and site_type_arr is not None:
                try:
                    st = site_type_arr[sid]
                    if not (pd.isna(st) or str(st) == str(cell_type)):
                        continue
                except:
                    pass
            
            # Score: distance from centroid (prefer spreading) - distance from current (not too far)
            dist_from_center = ((sx - centroid_x)**2 + (sy - centroid_y)**2) ** 0.5
            dist_from_current = abs(sx - cx) + abs(sy - cy)
            
            # Prefer sites that are far from center but not too far from current position
            score = dist_from_center - 0.3 * dist_from_current
            
            if score > best_score:
                best_score = score
                best_site = sid
        
        if best_site is not None:
            return (cell, best_site)
        return None
    
    # Normalize probabilities for three move types
    total_prob = p_refine + p_explore + p_relocate
    if total_prob > 0:
        p_refine_norm = p_refine / total_prob
        p_explore_norm = (p_refine + p_explore) / total_prob
        # p_relocate is the remaining probability
    else:
        p_refine_norm = 0.5
        p_explore_norm = 0.75
    
    accepted_moves = 0
    relocation_moves = 0
    
    for i in range(iters):
        # Choose move type based on probability
        move_type_rand = rng.random()
        is_relocate = False
        
        if move_type_rand < p_refine_norm:
            # Refine move: swap nearby cells
            move_result = _pick_refine_move_optimized(
                batch_cells, cell_pos_x, cell_pos_y, cell_to_idx, 
                refine_max_distance, rng
            )
        elif move_type_rand < p_explore_norm:
            # Explore move: swap cells within current window (using optimized version)
            move_result = _pick_explore_move_optimized(
                batch_cells, cell_pos_x, cell_pos_y, cell_to_idx, 
                window_size, rng
            )
        else:
            # Relocate move: move a cell to a free site in less dense area
            is_relocate = True
            relocate_result = _pick_relocate_move(rng)
            move_result = None  # Use relocate_result instead
        
        # Handle RELOCATE move separately (move to free site, not swap)
        if is_relocate:
            if relocate_result is None:
                continue
            
            cell, new_site = relocate_result
            old_site = assignments[cell]
            
            # Calculate HPWL before relocation
            nets_aff = cell_nets.get(cell, set())
            old_hpwl = _hpwl_for_nets_optimized(nets_aff, pos_cells, net_to_cells, fixed_pts)
            
            # Apply relocation
            old_x, old_y = pos_cells[cell]
            new_x, new_y = float(site_x_arr[new_site]), float(site_y_arr[new_site])
            assignments[cell] = new_site
            pos_cells[cell] = (new_x, new_y)
            
            # Update cell position arrays
            idx = cell_to_idx.get(cell)
            if idx is not None:
                cell_pos_x[idx] = new_x
                cell_pos_y[idx] = new_y
            
            # Calculate HPWL after relocation
            new_hpwl = _hpwl_for_nets_optimized(nets_aff, pos_cells, net_to_cells, fixed_pts)
            delta = new_hpwl - old_hpwl
            
            # Accept or reject based on SA criterion
            if delta < 0:
                accept = True
            else:
                if temp > 1e-9:
                    accept = rng.random() < math.exp(-delta / temp)
                else:
                    accept = False
            
            if accept:
                # Update free sites list
                free_sites.remove(new_site)
                free_sites.append(old_site)
                accepted_moves += 1
                relocation_moves += 1
            else:
                # Revert
                assignments[cell] = old_site
                pos_cells[cell] = (old_x, old_y)
                if idx is not None:
                    cell_pos_x[idx] = old_x
                    cell_pos_y[idx] = old_y
            
            continue
        
        # Handle SWAP moves (refine and explore)
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
        
        # Animation frame capture
        if frame_callback is not None and (i + 1) % frame_interval == 0:
            try:
                frame_callback(i + 1, cur, temp, relocation_moves)
            except Exception as e:
                pass  # Don't let animation errors break SA
            
        if (i + 1) % 200 == 0:
            print(f"        [SA] Iter {i+1}: T={temp:.3f} HPWL={cur:.1f} Acc={accepted_moves/(i+1):.1%} Reloc={relocation_moves}")

    print(f"      [SA] End Batch: {start_hpwl:.1f} -> {cur:.1f} ({cur-start_hpwl:+.1f}) Relocations={relocation_moves}")
    
    return (cur, relocation_moves)

