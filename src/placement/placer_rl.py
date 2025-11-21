"""
PPO-based placer + swap refiner for structured ASICs.

Drop into src/placement/ppo_placer.py and call the helpers at the bottom.
Requires: torch, numpy, pandas
"""

import math
import random
import time
from typing import List, Dict, Tuple, Set, Optional, Any, cast
import numpy as np
import pandas as pd
import csv

# Torch import
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.distributions import Categorical
except ImportError as e:
    raise ImportError(
        "PyTorch is required for PPO placer/refiner. Install with: pip install torch"
    ) from e

# -------------------------
# Helper functions (HPWL, site builders)
# -------------------------
def hpwl_of_nets(nets: Dict[int, Set[str]],
                 pos_cells: Dict[str, Tuple[float, float]],
                 fixed_pts: Dict[int, List[Tuple[float, float]]],
                 net_subset: Optional[Set[int]] = None,
                 net_weights: Optional[Dict[int, float]] = None) -> float:
    total = 0.0
    for net, cells in nets.items():
        if net_subset is not None and net not in net_subset:
            continue
        xs, ys = [], []
        for c in cells:
            if c in pos_cells:
                x, y = pos_cells[c]
                xs.append(x); ys.append(y)
        for (fx, fy) in fixed_pts.get(net, []):
            xs.append(fx); ys.append(fy)
        if len(xs) >= 2:
            wl = (max(xs) - min(xs)) + (max(ys) - min(ys))
            if net_weights is not None:
                wl *= float(net_weights.get(net, 1.0))
            total += wl
    return total

def build_sites_from_fabric_df(fabric_df: pd.DataFrame) -> pd.DataFrame:
    """Build sites DataFrame, preserving cell_type if available.
    Columns produced: site_id, x_um, y_um, tile_name (optional), cell_type (optional).
    """
    base_cols = [c for c in ["cell_x", "cell_y", "tile_name", "cell_type"] if c in fabric_df.columns]
    sites = fabric_df[base_cols].drop_duplicates().reset_index(drop=True).copy()
    sites.rename(columns={"cell_x": "x_um", "cell_y": "y_um"}, inplace=True)
    sites.insert(0, "site_id", range(len(sites)))
    return sites

def fixed_points_from_pins(pins_df: pd.DataFrame) -> Dict[int, List[Tuple[float,float]]]:
    fp: Dict[int, List[Tuple[float,float]]] = {}
    if not {"net_bit","x_um","y_um"}.issubset(pins_df.columns):
        return fp
    pins_valid = pins_df.dropna(subset=["net_bit","x_um","y_um"])
    for r in pins_valid.itertuples(index=False):
        nb = int(getattr(r,"net_bit"))
        x = float(getattr(r,"x_um")); y = float(getattr(r,"y_um"))
        fp.setdefault(nb,[]).append((x,y))
    return fp

def nets_map_from_graph_df(gdf: pd.DataFrame) -> Dict[int, Set[str]]:
    res: Dict[int, Set[str]] = {}
    if not {"net_bit","cell_name"}.issubset(gdf.columns):
        return res
    for nb, grp in gdf.dropna(subset=["net_bit"]).groupby("net_bit"):
        res[int(nb)] = set(grp["cell_name"].astype(str).tolist())
    return res

# -------------------------
# Environments
# -------------------------
class FullAssignEnv:
    """
    Sequential assigner: given an ordered list of cells and a set of legal sites,
    the agent selects a site for the current cell.

    - cells: list[str]
    - sites_list: list[(site_id, x, y)]
    - nets_map: net_bit -> set(cell_names)
    - fixed_pins: net_bit -> [(x,y), ...]
    - max_action (pad action logits to this size)
    """

    def __init__(self,
                 cells: List[str],
                 sites_list: List[Tuple[int,float,float]],
                 nets_map: Dict[int, Set[str]],
                 fixed_pins: Dict[int, List[Tuple[float,float]]],
                 start_assignments: Optional[Dict[str,int]] = None,
                 max_action: int = 32,
                 congestion_radius: float = 20.0,
                 global_reward_interval: int = 10,
                 global_reward_weight: float = 0.02,
                 site_types: Optional[List[str]] = None,
                 cell_types: Optional[Dict[str, str]] = None):
        self.cells = cells[:]  # assignment order
        self.sites_list = sites_list[:]  # index -> (site_id,x,y)
        self.site_index_by_id = {s[0]: idx for idx,s in enumerate(self.sites_list)}
        self.nets = nets_map
        self.fixed = fixed_pins
        self.site_types = site_types[:] if site_types is not None else None  # index-aligned list of site type strings
        if self.site_types is not None:
            assert len(self.site_types) == len(self.sites_list), "site_types length must match sites_list length"
        self.cell_types = dict(cell_types) if cell_types is not None else {}
        # Precompute site coordinate arrays for vectorized candidate ranking
        self.site_x = np.fromiter((s[1] for s in self.sites_list), dtype=float)
        self.site_y = np.fromiter((s[2] for s in self.sites_list), dtype=float)
        self.free_mask = np.ones(len(self.sites_list), dtype=bool)
        # Map from type -> numpy array of site indices for fast filtering
        self._indices_by_type: Dict[str, np.ndarray] = {}
        if self.site_types is not None:
            by_type: Dict[str, List[int]] = {}
            for idx, t in enumerate(self.site_types):
                by_type.setdefault(str(t), []).append(idx)
            for t, lst in by_type.items():
                self._indices_by_type[t] = np.asarray(lst, dtype=int)

        # limit action space to top-K candidates
        self.max_action = max_action
        self.congestion_radius = congestion_radius
        # observation dimension: 2 cell features + max_action * 4 site features
        # upgraded: 4 global/cell features + K*4 candidate features
        self._obs_dim = 4 + self.max_action * 4

        # placement state:
        # assignments: cell -> site_id (for all placed)
        self.assignments: Dict[str,int] = {} if start_assignments is None else dict(start_assignments)
        self.pos_cells: Dict[str, Tuple[float,float]] = {}
        for c,sid in self.assignments.items():
            idx = self.site_index_by_id[sid]
            _, x,y = self.sites_list[idx]
            self.pos_cells[c] = (x,y)
        # free site indices (indices into sites_list)
        self.free_site_idx = [i for i in range(len(self.sites_list)) if self.sites_list[i][0] not in set(self.assignments.values())]

        # precompute cell->nets
        self.cell_to_nets: Dict[str, Set[int]] = {}
        for nb, cells_set in self.nets.items():
            for c in cells_set:
                self.cell_to_nets.setdefault(c,set()).add(nb)

        # current step index
        self.step_idx = 0
        # cache of last candidate site indices (into sites_list) used to build obs
        self._last_candidates: List[int] = []
        # last obs and fixed obs dimension (2 + max_action*4)
        self._last_obs: np.ndarray = np.zeros(4 + max_action*4, dtype=np.float32)
        self._obs_dim: int = 4 + max_action*4
        # global reward bookkeeping
        self._global_reward_interval = max(1, int(global_reward_interval))
        self._global_reward_weight = float(global_reward_weight)
        self._global_hpwl_prev = None  # type: Optional[float]

    def reset(self):
        self.assignments = {}
        self.pos_cells = {}
        self.free_site_idx = [i for i in range(len(self.sites_list))]
        self.free_mask[:] = True
        self.step_idx = 0
        obs = self._obs()
        self._obs_dim = obs.shape[0]
        return obs

    def _obs(self) -> np.ndarray:
        """
        Build observation for current step (for the current cell).
        Representation:
         - current cell one-hot? (we will instead pass index)
         - sites features: for each candidate site up to max_action:
             [x_norm, y_norm, local_density, mean_net_bbox_size_of_cell_if_placed_here]
         - current cell features: deg, avg_net_bbox_of_cell
        We'll return a 1D vector with:
         [cell_features, flattened candidate_site_features (max_action*F)]
        If available candidates < max_action -> pad with large negative values and mask them.
        """
        cur_cell = self.cells[self.step_idx]
        # cell features
        deg = float(len(self.cell_to_nets.get(cur_cell, set())))
        # estimate avg bbox size for nets touching this cell under current pos
        bbox_vals = []
        for nb in self.cell_to_nets.get(cur_cell, set()):
            pts = []
            # placed cells
            for c in self.nets.get(nb, set()):
                if c in self.pos_cells:
                    pts.append(self.pos_cells[c])
            # fixed pins
            for fx,fy in self.fixed.get(nb, []):
                pts.append((fx,fy))
            if pts:
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                bbox_vals.append((max(xs)-min(xs)) + (max(ys)-min(ys)))
        avg_bbox = float(np.mean(bbox_vals)) if bbox_vals else 0.0

        # site candidates: choose top-K nearest to centroid of nets touching this cell, filtered by cell_type if available
        # Vectorized candidate derivation
        if self.site_types is not None and self.cell_types:
            ctype = self.cell_types.get(cur_cell)
            if ctype is not None and ctype in self._indices_by_type:
                cand_idx = self._indices_by_type[ctype]
                cand_idx = cand_idx[self.free_mask[cand_idx]]
            else:
                cand_idx = np.flatnonzero(self.free_mask)
        else:
            cand_idx = np.flatnonzero(self.free_mask)
        # Fallback if no free sites (should not happen mid-episode unless data issue)
        if cand_idx.size == 0:
            self._last_candidates = []
            padded = np.full((self.max_action, 4), -10.0, dtype=np.float32)
            cell_feat = np.array([deg, avg_bbox, 0.0, 0.0], dtype=np.float32)
            obs = np.concatenate([cell_feat, padded.flatten()], axis=0)
            self._last_obs = obs
            return obs
        # compute centroid of currently connected points for cur_cell
        pts_all = []
        for nb in self.cell_to_nets.get(cur_cell, set()):
            for c in self.nets.get(nb, set()):
                if c in self.pos_cells:
                    pts_all.append(self.pos_cells[c])
            for fx, fy in self.fixed.get(nb, []):
                pts_all.append((fx, fy))
        if pts_all:
            cx = float(np.mean([p[0] for p in pts_all])); cy = float(np.mean([p[1] for p in pts_all]))
        else:
            # fallback to center of all sites
            xs_all = [s[1] for s in self.sites_list]; ys_all = [s[2] for s in self.sites_list]
            cx = float(np.mean(xs_all)); cy = float(np.mean(ys_all))
        # rank free sites by distance to (cx,cy)
        dx = self.site_x[cand_idx] - cx
        dy = self.site_y[cand_idx] - cy
        dist2 = dx*dx + dy*dy
        if cand_idx.size > self.max_action:
            # argpartition gives indices of k smallest without full sort
            part = np.argpartition(dist2, self.max_action)[:self.max_action]
            candidates = cand_idx[part]
            # order them for determinism
            order = np.argsort(dist2[part])
            candidates = candidates[order]
        else:
            order = np.argsort(dist2)
            candidates = cand_idx[order]
        candidates = candidates.tolist()
        # remember candidate indices so action index maps correctly
        self._last_candidates = candidates[:self.max_action]
        # build features per candidate
        feats = []
        for idx in candidates:
            sx = float(self.site_x[idx]); sy = float(self.site_y[idx])
            # local density = count placed cells within radius
            cnt = 0
            for (px,py) in self.pos_cells.values():
                if (px-sx)**2 + (py-sy)**2 <= (self.congestion_radius**2):
                    cnt += 1
            # if placed here, estimate new bbox for nets touching cur_cell
            est_bbox = 0.0
            for nb in self.cell_to_nets.get(cur_cell, set()):
                pts = []
                # placed cells
                for c in self.nets.get(nb, set()):
                    if c in self.pos_cells:
                        pts.append(self.pos_cells[c])
                # add this candidate position
                pts.append((sx,sy))
                for fx,fy in self.fixed.get(nb, []):
                    pts.append((fx,fy))
                if pts:
                    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                    est_bbox += (max(xs)-min(xs)) + (max(ys)-min(ys))
            if self.cell_to_nets.get(cur_cell):
                est_bbox /= len(self.cell_to_nets[cur_cell])
            feats.append([sx, sy, float(cnt), est_bbox])

        # normalization: center and scale by ranges for stability
        arr = np.array(feats, dtype=np.float32) if feats else np.zeros((0,4), dtype=np.float32)
        if arr.size:
            # normalize positions by centroid and span
            posx = arr[:,0]; posy = arr[:,1]
            cx = posx.mean(); cy = posy.mean()
            span = max((posx.max()-posx.min()), (posy.max()-posy.min()), 1.0)
            arr[:,0] = (arr[:,0]-cx)/span
            arr[:,1] = (arr[:,1]-cy)/span
            # normalize counts and bbox
            max_cnt = arr[:,2].max() if arr[:,2].size and arr[:,2].max()>0 else 1.0
            max_bbox = arr[:,3].max() if arr[:,3].size and arr[:,3].max()>0 else 1.0
            arr[:,2] = arr[:,2] / max_cnt
            arr[:,3] = arr[:,3] / max_bbox
        # pad up to max_action
        F = 4
        padded = np.full((self.max_action, F), -10.0, dtype=np.float32)
        for i,row in enumerate(arr[:self.max_action]):
            padded[i,:] = row
        # global signals: avg density around placed cells and normalized total HPWL
        # avg local density
        if self.pos_cells:
            pcs = list(self.pos_cells.values())
            dens_vals = []
            for (sx,sy) in pcs:
                dens_vals.append(sum(1 for (px,py) in pcs if (px-sx)**2 + (py-sy)**2 <= (self.congestion_radius**2)) - 1)
            g_dens = float(np.mean(dens_vals)) if dens_vals else 0.0
        else:
            g_dens = 0.0
        # normalized HPWL over all placed cells and fixed pins
        hpwl_now = hpwl_of_nets(self.nets, self.pos_cells, self.fixed)
        hpwl_norm = float(hpwl_now / (len(self.nets) + 1e-6))
        # final observation vector
        cell_feat = np.array([deg, avg_bbox, g_dens, hpwl_norm], dtype=np.float32)
        obs = np.concatenate([cell_feat, padded.flatten()], axis=0)
        self._last_obs = obs
        return obs

    def action_mask(self) -> np.ndarray:
        # mask shape (max_action,), 1 = legal, 0 = illegal, aligned with last candidates
        mask = np.zeros(self.max_action, dtype=np.float32)
        n = min(len(self._last_candidates), self.max_action)
        if n > 0:
            mask[:n] = 1.0
        return mask

    def step(self, action: int) -> Tuple[np.ndarray, float, bool]:
        """
        action is index into padded candidate list (0..max_action-1).
        If masked illegal action chosen -> heavy negative reward and stay.
        """
        mask = self.action_mask()
        if action < 0 or action >= self.max_action or mask[action] < 0.5:
            # illegal
            # small penalty for illegal moves
            return self._obs(), -1.0, False

        # real site index from last observed candidates
        if action >= len(self._last_candidates):
            return self._obs(), -1.0, False
        site_idx = self._last_candidates[action]
        site_id = self.sites_list[site_idx][0]
        sx = float(self.site_x[site_idx]); sy = float(self.site_y[site_idx])
        cur_cell = self.cells[self.step_idx]

        # compute local nets touched and hpwl before
        nets_touch = set(self.cell_to_nets.get(cur_cell, set()))
        total_before = hpwl_of_nets(self.nets, self.pos_cells, self.fixed, net_subset=nets_touch)

        # assign
        self.assignments[cur_cell] = site_id
        self.pos_cells[cur_cell] = (sx,sy)
        # remove chosen site index from free_site_idx
        # Mark site as used (free_mask) and lazily skip costly list removal if large
        self.free_mask[site_idx] = False
        if site_idx in self.free_site_idx:
            self.free_site_idx.remove(site_idx)

        # delta hpwl for touched nets (local)
        total_after = hpwl_of_nets(self.nets, self.pos_cells, self.fixed, net_subset=nets_touch)
        d_local = total_after - total_before
        reward = -d_local
        # optionally penalize if local density > threshold
        local_density = sum(1 for (px,py) in self.pos_cells.values() if (px-sx)**2 + (py-sy)**2 <= (self.congestion_radius**2))
        if local_density > 6:
            reward -= 0.1 * (local_density - 6)

        # periodic global HPWL reward injection
        if self._global_hpwl_prev is None:
            self._global_hpwl_prev = hpwl_of_nets(self.nets, self.pos_cells, self.fixed)

        done = False
        # advance step
        self.step_idx += 1
        if self.step_idx >= len(self.cells):
            done = True
        # every N steps or at episode end, add global delta
        if done or (self.step_idx % self._global_reward_interval == 0):
            g_now = hpwl_of_nets(self.nets, self.pos_cells, self.fixed)
            g_delta = g_now - (self._global_hpwl_prev if self._global_hpwl_prev is not None else g_now)
            reward += - self._global_reward_weight * float(g_delta)
            self._global_hpwl_prev = g_now
        if done:
            term_obs = np.zeros(self._obs_dim, dtype=np.float32)
            return term_obs, float(reward), True
        return self._obs(), float(reward), False

    def current_assignment(self) -> Dict[str,int]:
        # returns mapping cell -> site_id
        return dict(self.assignments)

class SwapRefineEnv:
    """
    Swap-based entry: given a batch (fixed size), the agent picks a pair (i,j) to swap.
    Batch size defines action space size = B*(B-1)/2 + 1 (no-op).
    Cell/site type awareness: a swap is only legal if each cell's type matches the destination site's type.
    """

    def __init__(self,
                 batch_cells: List[str],
                 placement_map: Dict[str, Tuple[float,float,int]],  # cell_name -> (x,y,site_id)
                 sites_map: Dict[int, Tuple[float,float]],
                 nets_map: Dict[int, Set[str]],
                 fixed_pins: Dict[int, List[Tuple[float,float]]],
                 neighbor_radius: float = 20.0,
                 congestion_weight: float = 0.02,
                 net_weight_alpha: float = 0.1,
                 target_B: Optional[int] = None,
                 site_types_map: Optional[Dict[int, str]] = None,
                 cell_types_map: Optional[Dict[str, str]] = None):
        self.batch = batch_cells[:]
        self.placement = dict(placement_map)  # shallow copy
        self.sites_map = dict(sites_map)
        self.nets = nets_map
        self.fixed = fixed_pins
        self.neighbor_radius = neighbor_radius
        self.congestion_weight = congestion_weight
        self.B = len(self.batch)
        self.target_B = target_B if target_B is not None else self.B
        # precompute mapping and nets
        self.cell_to_nets = {c: set() for c in self.batch}
        for nb, cs in self.nets.items():
            for c in cs:
                if c in self.cell_to_nets:
                    self.cell_to_nets[c].add(nb)

        # type maps
        self.site_types_map = dict(site_types_map) if site_types_map is not None else {}
        self.cell_types_map = dict(cell_types_map) if cell_types_map is not None else {}

        # net weights by pin count as a simple timing/congestion proxy
        self.net_weights: Dict[int, float] = {}
        for nb, cs in self.nets.items():
            sz = max(0, len(cs) - 2)
            self.net_weights[nb] = 1.0 + net_weight_alpha * float(sz)

        # action list: pairs
        self.action_pairs = [(i,j) for i in range(self.B) for j in range(i+1, self.B)]
        self.action_pairs.append((-1,-1))  # no-op
        # precompute legal mask
        self._action_mask = self._compute_action_mask()

    def _compute_action_mask(self) -> np.ndarray:
        mask = np.zeros(len(self.action_pairs), dtype=np.float32)
        for idx,(i,j) in enumerate(self.action_pairs):
            if i == -1 and j == -1:
                mask[idx] = 1.0
                continue
            ci = self.batch[i]; cj = self.batch[j]
            xi, yi, sidi = self.placement[ci]
            xj, yj, sidj = self.placement[cj]
            # cell ci would move to sidj; cell cj to sidi
            if self._is_type_compatible(ci, sidj) and self._is_type_compatible(cj, sidi):
                mask[idx] = 1.0
        return mask

    def _is_type_compatible(self, cell: str, site_id: int) -> bool:
        if not self.cell_types_map or not self.site_types_map:
            return True
        ctype = self.cell_types_map.get(cell)
        stype = self.site_types_map.get(site_id)
        if ctype is None or stype is None:
            return True
        return ctype == stype

    def action_mask(self) -> np.ndarray:
        return self._action_mask.copy()

    def reset(self):
        return self._obs()

    def _obs(self) -> np.ndarray:
        if self.B == 0:
            return np.zeros(self.target_B*4, dtype=np.float32)
        coords = [ (self.placement[c][0], self.placement[c][1]) for c in self.batch ]
        xs = np.array([p[0] for p in coords], dtype=np.float32)
        ys = np.array([p[1] for p in coords], dtype=np.float32)
        cx, cy = xs.mean(), ys.mean()
        span = max((xs.max()-xs.min()), (ys.max()-ys.min()), 1.0)
        xs_n = (xs-cx)/span
        ys_n = (ys-cy)/span
        deg = np.array([len(self.cell_to_nets[c]) for c in self.batch], dtype=np.float32)
        # local density
        dens = np.zeros(self.B, dtype=np.float32)
        for i,(x,y) in enumerate(coords):
            dens[i] = sum(1 for (x2,y2) in coords if (x-x2)**2 + (y-y2)**2 <= (self.neighbor_radius**2)) - 1
        deg_n = deg / (deg.max() if deg.max()>0 else 1.0)
        dens_n = dens / (dens.max() if dens.max()>0 else 1.0)
        feat = np.stack([xs_n, ys_n, deg_n, dens_n], axis=1)  # shape Bx4
        if self.B < self.target_B:
            pad_rows = np.zeros((self.target_B - self.B, 4), dtype=np.float32)
            feat = np.concatenate([feat, pad_rows], axis=0)
        elif self.B > self.target_B:
            feat = feat[:self.target_B, :]
        return feat.flatten()

    def step(self, action_idx: int) -> Tuple[np.ndarray, float, bool]:
        if action_idx < 0 or action_idx >= len(self.action_pairs):
            return self._obs(), -0.01, False
        if self._action_mask[action_idx] < 0.5:
            # illegal swap due to type mismatch
            return self._obs(), -0.1, False
        i,j = self.action_pairs[action_idx]
        if i==-1 and j==-1:
            self._action_mask = self._compute_action_mask()
            return self._obs(), 0.0, False
        ci = self.batch[i]; cj = self.batch[j]
        xi, yi, sidi = self.placement[ci]
        xj, yj, sidj = self.placement[cj]
        nets_aff = set(self.cell_to_nets.get(ci,set())) | set(self.cell_to_nets.get(cj,set()))
        before = hpwl_of_nets(self.nets, {c:(self.placement[c][0], self.placement[c][1]) for c in self.batch}, self.fixed, net_subset=nets_aff, net_weights=self.net_weights)
        # swap
        self.placement[ci] = (xj, yj, sidj)
        self.placement[cj] = (xi, yi, sidi)
        after = hpwl_of_nets(self.nets, {c:(self.placement[c][0], self.placement[c][1]) for c in self.batch}, self.fixed, net_subset=nets_aff, net_weights=self.net_weights)
        d_hpwl = after - before
        # congestion-aware penalty: change in local density around the swapped locations
        def _density_at(x: float, y: float) -> int:
            return sum(1 for (xx,yy,_) in self.placement.values() if (xx-x)**2 + (yy-y)**2 <= (self.neighbor_radius**2)) - 1
        dens_before = _density_at(xi, yi) + _density_at(xj, yj)
        dens_after = _density_at(self.placement[ci][0], self.placement[ci][1]) + _density_at(self.placement[cj][0], self.placement[cj][1])
        d_dens = dens_after - dens_before
        reward = -d_hpwl - self.congestion_weight * float(d_dens)
        # refresh mask after state change
        self._action_mask = self._compute_action_mask()
        return self._obs(), float(reward), False

# -------------------------
# PPO Agent (shared actor-critic MLP)
# -------------------------
class MLPPolicy(nn.Module):
    def __init__(self, obs_dim: int, hidden: int = 256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
        )
        # actor head will be separate (action_dim provided externally)
    def forward(self, x):
        return self.net(x)

class PPOAgent:
    def __init__(self, obs_dim: int, action_dim: int, hidden: int = 256, lr: float = 3e-4, device: str = "cpu",
                 clip_eps: float = 0.2, value_coef: float = 1.0, entropy_coef: float = 0.01, max_grad_norm: float = 0.5):
        self.device = torch.device(device)
        self.obs_dim = obs_dim
        self.policy_backbone = MLPPolicy(obs_dim, hidden).to(self.device)
        # actor and critic heads (we instantiate actor with largest action_dim encountered)
        self.actor = nn.Linear(hidden, action_dim).to(self.device)
        self.critic = nn.Linear(hidden, 1).to(self.device)
        self.optimizer = optim.Adam(list(self.policy_backbone.parameters()) + list(self.actor.parameters()) + list(self.critic.parameters()), lr=lr)

        self.clip_eps = float(clip_eps)
        self.value_coef = float(value_coef)
        self.entropy_coef = float(entropy_coef)
        self.max_grad_norm = float(max_grad_norm)
        self.device = torch.device(device)

    def forward(self, obs: np.ndarray):
        if obs.shape[0] != self.obs_dim:
            if obs.shape[0] < self.obs_dim:
                pad = np.zeros(self.obs_dim - obs.shape[0], dtype=np.float32)
                obs = np.concatenate([obs, pad], axis=0)
            else:
                obs = obs[:self.obs_dim]
        t = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)
        h = self.policy_backbone(t)
        logits = self.actor(h).squeeze(0)
        value = self.critic(h).squeeze(0)
        return logits, value

    def get_action_and_value(self, obs: np.ndarray, mask: Optional[np.ndarray]=None, eps: float = 0.0):
        # Pure policy sampling; remove ε-greedy to keep PPO ratios unbiased
        logits, value = self.forward(obs)
        if mask is not None:
            mask_t = torch.tensor(mask, dtype=torch.bool, device=self.device)
            logits = logits.masked_fill(~mask_t, float('-1e9'))
        probs = torch.softmax(logits, dim=0)
        dist = Categorical(probs)
        act_t = dist.sample()
        act = int(act_t.item())
        logp = dist.log_prob(act_t)
        return act, float(logp.item()), float(value.item())

    def compute_loss_and_update(self, batch_obs, batch_actions, batch_logps_old, batch_returns, batch_advantages, masks=None):
        fixed = []
        for o in batch_obs:
            if o.shape[0] != self.obs_dim:
                if o.shape[0] < self.obs_dim:
                    pad = np.zeros(self.obs_dim - o.shape[0], dtype=np.float32)
                    o = np.concatenate([o, pad], axis=0)
                else:
                    o = o[:self.obs_dim]
            fixed.append(o)
        obs = torch.tensor(np.stack(fixed), dtype=torch.float32, device=self.device)
        actions = torch.tensor(batch_actions, dtype=torch.int64, device=self.device)
        old_logps = torch.tensor(batch_logps_old, dtype=torch.float32, device=self.device)
        returns = torch.tensor(batch_returns, dtype=torch.float32, device=self.device)
        advs = torch.tensor(batch_advantages, dtype=torch.float32, device=self.device)

        h = self.policy_backbone(obs)
        logits = self.actor(h)
        if masks is not None:
            msk_t = torch.tensor(masks, dtype=torch.bool, device=self.device)
            logits = logits.masked_fill(~msk_t, float('-1e9'))
        values = self.critic(h).squeeze(1)

        # compute log probs
        # create distribution per-row with masked logits
        probs = torch.softmax(logits, dim=1)
        m = Categorical(probs)
        new_logps = m.log_prob(actions)
        entropy = m.entropy().mean()

        ratio = torch.exp(new_logps - old_logps)
        surr1 = ratio * advs
        surr2 = torch.clamp(ratio, 1.0 - self.clip_eps, 1.0 + self.clip_eps) * advs
        policy_loss = -torch.min(surr1, surr2).mean()

        value_loss = nn.functional.mse_loss(values, returns)

        loss = policy_loss + self.value_coef * value_loss - self.entropy_coef * entropy

        self.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(list(self.policy_backbone.parameters()) + list(self.actor.parameters()) + list(self.critic.parameters()), self.max_grad_norm)
        self.optimizer.step()
        return loss.item(), policy_loss.item(), value_loss.item(), entropy.item()

# -------------------------
# PPO training helpers (GAE)
# -------------------------
def compute_gae(rewards, values, dones, gamma=0.99, lam=0.95):
    advs = []
    gae = 0.0
    values = values + [0.0]
    for t in reversed(range(len(rewards))):
        delta = rewards[t] + gamma * values[t+1] * (1.0 - dones[t]) - values[t]
        gae = delta + gamma * lam * (1.0 - dones[t]) * gae
        advs.insert(0, gae)
    returns = [ad + v for ad,v in zip(advs, values[:-1])]
    return returns, advs

# -------------------------
# High-level train loops
# -------------------------
def train_ppo_full_placer(env_builder_fn,   # function that returns a fresh FullAssignEnv
                          agent: PPOAgent,
                          total_episodes: int = 200,
                          steps_per_episode: int = 512,
                          batch_size: int = 64,
                          eps_start: float = 0.3,
                          eps_end: float = 0.02,
                          device: str = "cpu",
                          ppo_epochs: int = 4,
                          mini_batch_size: int = 128,
                          log_csv_path: Optional[str] = None):
    """
    env_builder_fn() -> FullAssignEnv. We'll run episodes, collect rollout, compute GAE, and update PPO.
    This is a simple on-policy training loop suited for experiments.
    """
    agent.device = torch.device(device)
    # init CSV header if requested
    if log_csv_path is not None:
        try:
            with open(log_csv_path, "a", newline="") as f:
                if f.tell() == 0:
                    w = csv.writer(f)
                    w.writerow(["kind","episode","loss","policy_loss","value_loss","entropy","steps","eps","hpwl_end","time_sec"])
        except Exception:
            pass
    for ep in range(total_episodes):
        t_ep_start = time.perf_counter()
        env = env_builder_fn()
        obs = env.reset()
        obs_list, act_list, logp_list, val_list, rew_list, done_list = [], [], [], [], [], []
        eps = max(eps_end, eps_start * (1.0 - ep/total_episodes))
        done = False
        steps = 0
        while not done and steps < steps_per_episode:
            mask = env.action_mask()
            a, logp, val = agent.get_action_and_value(obs, mask=mask, eps=eps)
            obs2, r, done = env.step(a)
            obs_list.append(obs); act_list.append(a); logp_list.append(logp); val_list.append(val)
            rew_list.append(r); done_list.append(float(done))
            obs = obs2
            steps += 1
        # compute returns and advantages
        returns, advs = compute_gae(rew_list, val_list, done_list, gamma=0.99, lam=0.95)
        # Advantage normalization for PPO stability
        if len(advs) > 1:
            adv_arr = np.array(advs, dtype=np.float32)
            adv_arr = (adv_arr - adv_arr.mean()) / (adv_arr.std() + 1e-8)
            advs = adv_arr.tolist()
        # multi-epoch, mini-batch PPO updates
        idxs = np.arange(len(act_list))
        losses = []
        plosses = []
        vlosses = []
        ents = []
        for _ in range(max(1, int(ppo_epochs))):
            np.random.shuffle(idxs)
            for start in range(0, len(idxs), max(1, int(mini_batch_size))):
                mb = idxs[start:start+max(1, int(mini_batch_size))]
                mb_obs = [obs_list[i] for i in mb]
                mb_act = [act_list[i] for i in mb]
                mb_logp = [logp_list[i] for i in mb]
                mb_ret = [returns[i] for i in mb]
                mb_adv = [advs[i] for i in mb]
                loss, ploss, vloss, ent = agent.compute_loss_and_update(
                    batch_obs=mb_obs,
                    batch_actions=mb_act,
                    batch_logps_old=mb_logp,
                    batch_returns=mb_ret,
                    batch_advantages=mb_adv
                )
                losses.append(loss); plosses.append(ploss); vlosses.append(vloss); ents.append(ent)
        # simple running print
        loss = float(np.mean(losses)) if losses else 0.0
        ploss = float(np.mean(plosses)) if plosses else 0.0
        vloss = float(np.mean(vlosses)) if vlosses else 0.0
        ent = float(np.mean(ents)) if ents else 0.0
        t_ep_end = time.perf_counter()
        # episode HPWL (over currently placed cells only)
        try:
            hpwl_end = hpwl_of_nets(env.nets, env.pos_cells, env.fixed)
        except Exception:
            hpwl_end = float("nan")
        if log_csv_path is not None:
            try:
                with open(log_csv_path, "a", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["full", ep+1, f"{loss:.6f}", f"{ploss:.6f}", f"{vloss:.6f}", f"{ent:.6f}", steps, f"{eps:.4f}", f"{hpwl_end:.6f}", f"{(t_ep_end - t_ep_start):.6f}"])
            except Exception:
                pass
        if (ep+1) % 10 == 0:
            print(f"[FullPPO] ep {ep+1}/{total_episodes} loss={loss:.4f} policy={ploss:.4f} value={vloss:.4f} ent={ent:.4f}")

def train_ppo_swap_refiner(env_builder_fn, agent: PPOAgent,
                           episodes: int = 200, steps_per_episode: int = 100, device: str = "cpu",
                           ppo_epochs: int = 4, mini_batch_size: int = 128,
                           log_csv_path: Optional[str] = None):
    """
    Similar to train_ppo_full_placer but for SwapRefineEnv where action_dim is fixed by batch.
    env_builder_fn should return a fresh SwapRefineEnv.
    """
    agent.device = torch.device(device)
    # init CSV header if requested
    if log_csv_path is not None:
        try:
            with open(log_csv_path, "a", newline="") as f:
                if f.tell() == 0:
                    w = csv.writer(f)
                    w.writerow(["kind","episode","loss","policy_loss","value_loss","entropy","steps","hpwl_local_end","time_sec"])
        except Exception:
            pass
    for ep in range(episodes):
        t_ep_start = time.perf_counter()
        env = env_builder_fn()
        obs = env.reset()
        obs_list, act_list, logp_list, val_list, rew_list, done_list = [], [], [], [], [], []
        done = False
        steps = 0
        while not done and steps < steps_per_episode:
            mask = env.action_mask()
            a, logp, val = agent.get_action_and_value(obs, mask=mask, eps=0.1)
            obs2, r, _ = env.step(a)
            obs_list.append(obs); act_list.append(a); logp_list.append(logp); val_list.append(val)
            rew_list.append(r); done_list.append(0.0)
            obs = obs2
            steps += 1
        # GAE
        returns, advs = compute_gae(rew_list, val_list, done_list)
        # Advantage normalization
        if len(advs) > 1:
            adv_arr = np.array(advs, dtype=np.float32)
            adv_arr = (adv_arr - adv_arr.mean()) / (adv_arr.std() + 1e-8)
            advs = adv_arr.tolist()
        # multi-epoch, mini-batch PPO updates
        idxs = np.arange(len(act_list))
        losses = []
        for _ in range(max(1, int(ppo_epochs))):
            np.random.shuffle(idxs)
            for start in range(0, len(idxs), max(1, int(mini_batch_size))):
                mb = idxs[start:start+max(1, int(mini_batch_size))]
                mb_obs = [obs_list[i] for i in mb]
                mb_act = [act_list[i] for i in mb]
                mb_logp = [logp_list[i] for i in mb]
                mb_ret = [returns[i] for i in mb]
                mb_adv = [advs[i] for i in mb]
                loss, ploss, vloss, ent = agent.compute_loss_and_update(mb_obs, mb_act, mb_logp, mb_ret, mb_adv, None)
                losses.append(loss)
        t_ep_end = time.perf_counter()
        # approximate batch-local HPWL at episode end
        try:
            # env.placement exists with (x,y,sid) for batch cells
            pos_map = {c: (env.placement[c][0], env.placement[c][1]) for c in env.batch}
            nets_touch: Set[int] = set()
            for c in env.batch:
                nets_touch |= env.cell_to_nets.get(c, set())
            hpwl_local = hpwl_of_nets(env.nets, pos_map, env.fixed, net_subset=nets_touch)
        except Exception:
            hpwl_local = float("nan")
        if log_csv_path is not None:
            try:
                with open(log_csv_path, "a", newline="") as f:
                    w = csv.writer(f)
                    mean_loss = float(np.mean(losses)) if losses else 0.0
                    w.writerow(["swap", ep+1, f"{mean_loss:.6f}", "", "", "", steps_per_episode, f"{hpwl_local:.6f}", f"{(t_ep_end - t_ep_start):.6f}"])
            except Exception:
                pass
        if (ep+1) % 10 == 0:
            mean_loss = float(np.mean(losses)) if losses else 0.0
            print(f"[SwapPPO] ep {ep+1}/{episodes} loss={mean_loss:.4f}")

# -------------------------
# Optional: BC pretraining for swap refiner
# -------------------------
def pretrain_bc_swap_refiner(env_builder_fn,
                             agent: PPOAgent,
                             epochs: int = 5,
                             steps_per_episode: int = 100,
                             device: str = "cpu"):
    """
    Supervised behavior cloning for SwapRefineEnv.
    At each step, label the best immediate-reward action (greedy oracle) and train actor via CE.
    """
    agent.device = torch.device(device)
    ce = nn.CrossEntropyLoss()
    for ep in range(max(1, int(epochs))):
        env = env_builder_fn()
        obs = env.reset()
        # Collect one episode of (obs, best_action)
        xs: List[np.ndarray] = []
        ys: List[int] = []
        steps = 0
        while steps < steps_per_episode:
            # label via one-step lookahead across all legal actions (type-compatible)
            mask = env.action_mask()
            legal_indices = [aidx for aidx,mv in enumerate(mask) if mv > 0.5]
            best_a = legal_indices[-1] if legal_indices else (len(env.action_pairs)-1)
            best_r = -1e9
            snap = {c: env.placement[c] for c in env.batch}
            for aidx in legal_indices:
                i,j = env.action_pairs[aidx]
                if i == -1 and j == -1:
                    _, r0, _ = env.step(aidx)
                    env.placement = {c: snap[c] for c in env.batch}
                    if r0 > best_r:
                        best_r = r0; best_a = aidx
                    continue
                ci = env.batch[i]; cj = env.batch[j]
                xi, yi, sidi = env.placement[ci]; xj, yj, sidj = env.placement[cj]
                nets_aff = set(env.cell_to_nets.get(ci,set())) | set(env.cell_to_nets.get(cj,set()))
                before = hpwl_of_nets(env.nets, {c:(env.placement[c][0], env.placement[c][1]) for c in env.batch}, env.fixed, net_subset=nets_aff, net_weights=getattr(env, 'net_weights', None))
                env.placement[ci] = (xj, yj, sidj)
                env.placement[cj] = (xi, yi, sidi)
                after = hpwl_of_nets(env.nets, {c:(env.placement[c][0], env.placement[c][1]) for c in env.batch}, env.fixed, net_subset=nets_aff, net_weights=getattr(env, 'net_weights', None))
                d_hpwl = after - before
                def _density_at(x: float, y: float) -> int:
                    return sum(1 for (xx,yy,_) in env.placement.values() if (xx-x)**2 + (yy-y)**2 <= (env.neighbor_radius**2)) - 1
                dens_before = _density_at(xi, yi) + _density_at(xj, yj)
                dens_after = _density_at(env.placement[ci][0], env.placement[ci][1]) + _density_at(env.placement[cj][0], env.placement[cj][1])
                d_dens = dens_after - dens_before
                r = -d_hpwl - env.congestion_weight * float(d_dens)
                env.placement[ci] = (xi, yi, sidi); env.placement[cj] = (xj, yj, sidj)
                if r > best_r:
                    best_r = r; best_a = aidx
            xs.append(obs); ys.append(int(best_a))
            # step env with best action to update state distribution
            obs, _, _ = env.step(best_a)
            steps += 1
        # train actor on collected dataset
        # pad/truncate obs inside agent.forward
        X = []
        for o in xs:
            if o.shape[0] != agent.obs_dim:
                if o.shape[0] < agent.obs_dim:
                    pad = np.zeros(agent.obs_dim - o.shape[0], dtype=np.float32)
                    o = np.concatenate([o, pad], axis=0)
                else:
                    o = o[:agent.obs_dim]
            X.append(o)
        X_t = torch.tensor(np.stack(X), dtype=torch.float32, device=agent.device)
        H = agent.policy_backbone(X_t)
        logits = agent.actor(H)
        y_t = torch.tensor(ys, dtype=torch.long, device=agent.device)
        loss = ce(logits, y_t)
        agent.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(list(agent.policy_backbone.parameters()) + list(agent.actor.parameters()), agent.max_grad_norm)
        agent.optimizer.step()
        print(f"[SwapBC] epoch {ep+1}/{epochs} ce_loss={float(loss.item()):.4f}")

# -------------------------
# Integration helpers with your repo
# -------------------------
# The following helpers show how to build env_builder functions and apply trained agents.
# They use existing functions you already have: assign_ports_to_pins, build_dependency_levels,
# place_cells_greedy_sim_anneal. Import them at your caller site and pass to these helpers.

def build_full_assign_env_from_data(cells_order: List[str],
                                    sites_df: pd.DataFrame,
                                    netlist_graph: pd.DataFrame,
                                    pins_df: pd.DataFrame,
                                    start_assignments: Optional[Dict[str,int]] = None,
                                    max_action: int = 1024) -> FullAssignEnv:
    """
    Build FullAssignEnv. cells_order is the placement sequence (e.g., levelized).
    sites_df: DataFrame from build_sites_from_fabric_df
    """
    # prepare structures
    sites_list = [(int(r.site_id), float(r.x_um), float(r.y_um)) for r in sites_df.itertuples(index=False)]
    site_types: Optional[List[str]] = None
    if 'cell_type' in sites_df.columns:
        site_types = [str(ct) for ct in sites_df['cell_type'].astype(str).tolist()]
    nets_map = nets_map_from_graph_df(netlist_graph)
    fixed = fixed_points_from_pins(pins_df)
    # build cell_types mapping if available in netlist_graph
    cell_types: Dict[str, str] = {}
    if 'cell_type' in netlist_graph.columns:
        for r in netlist_graph[['cell_name','cell_type']].dropna().itertuples(index=False):
            cell_types[str(getattr(r,'cell_name'))] = str(getattr(r,'cell_type'))
    env = FullAssignEnv(cells=cells_order,
                        sites_list=sites_list,
                        nets_map=nets_map,
                        fixed_pins=fixed,
                        start_assignments=start_assignments,
                        max_action=max_action,
                        site_types=site_types,
                        cell_types=cell_types if cell_types else None)
    return env

def apply_full_placer_agent(agent: PPOAgent, env: FullAssignEnv, eps: float = 0.0) -> Dict[str,int]:
    obs = env.reset()
    done = False
    while not done:
        mask = env.action_mask()
        a, logp, val = agent.get_action_and_value(obs, mask=mask, eps=eps)
        obs, r, done = env.step(a)
    return env.current_assignment()

def build_swap_refine_env_from_batch(batch_cells: List[str],
                                     placement_map: Dict[str, Tuple[float,float,int]],
                                     sites_map: Dict[int, Tuple[float,float]],
                                     netlist_graph: pd.DataFrame,
                                     pins_df: pd.DataFrame,
                                     site_types_map: Optional[Dict[int,str]] = None,
                                     cell_types_map: Optional[Dict[str,str]] = None) -> SwapRefineEnv:
    nets_map = nets_map_from_graph_df(netlist_graph)
    fixed = fixed_points_from_pins(pins_df)
    env = SwapRefineEnv(batch_cells, placement_map, sites_map, nets_map, fixed,
                        site_types_map=site_types_map, cell_types_map=cell_types_map)
    return env

def apply_swap_refiner(agent: PPOAgent, batch_cells: List[str], placement_map: Dict[str, Tuple[float,float,int]], sites_map: Dict[int, Tuple[float,float]], netlist_graph: pd.DataFrame, pins_df: pd.DataFrame, steps: int = 100, site_types_map: Optional[Dict[int,str]] = None, cell_types_map: Optional[Dict[str,str]] = None):
    env = build_swap_refine_env_from_batch(batch_cells, placement_map, sites_map, netlist_graph, pins_df, site_types_map=site_types_map, cell_types_map=cell_types_map)
    obs = env.reset()
    # Precompute local nets touching this batch for logging
    nets_touch: Set[int] = set()
    for c in batch_cells:
        nets_touch |= env.cell_to_nets.get(c, set())
    # Build pos map for local HPWL logging (batch-only positions)
    def _pos_map() -> Dict[str, Tuple[float,float]]:
        return {c: (env.placement[c][0], env.placement[c][1]) for c in batch_cells}
    hpwl_before = hpwl_of_nets(env.nets, _pos_map(), env.fixed, net_subset=nets_touch)
    for _ in range(steps):
        mask = env.action_mask()
        a, logp, v = agent.get_action_and_value(obs, mask=mask, eps=0.0)
        obs, r, _ = env.step(a)
    # greedy hill-climb: try a few best improving swaps
    def _local_delta(i: int, j: int) -> float:
        ci = env.batch[i]; cj = env.batch[j]
        xi, yi, sidi = env.placement[ci]; xj, yj, sidj = env.placement[cj]
        nets_aff = set(env.cell_to_nets.get(ci,set())) | set(env.cell_to_nets.get(cj,set()))
        before = hpwl_of_nets(env.nets, {c:(env.placement[c][0], env.placement[c][1]) for c in env.batch}, env.fixed, net_subset=nets_aff, net_weights=getattr(env, 'net_weights', None))
        # swap virtually
        env.placement[ci] = (xj, yj, sidj)
        env.placement[cj] = (xi, yi, sidi)
        after = hpwl_of_nets(env.nets, {c:(env.placement[c][0], env.placement[c][1]) for c in env.batch}, env.fixed, net_subset=nets_aff, net_weights=getattr(env, 'net_weights', None))
        # revert
        env.placement[ci] = (xi, yi, sidi); env.placement[cj] = (xj, yj, sidj)
        return after - before
    limit = min(20, max(1, len(batch_cells)//2))
    while limit > 0:
        best_pair = None
        best_delta = 0.0
        for i in range(len(batch_cells)):
            for j in range(i+1, len(batch_cells)):
                d = _local_delta(i,j)
                if d < best_delta:
                    best_delta = d; best_pair = (i,j)
        if best_pair is None:
            break
        i,j = best_pair
        ci = env.batch[i]; cj = env.batch[j]
        xi, yi, sidi = env.placement[ci]; xj, yj, sidj = env.placement[cj]
        env.placement[ci] = (xj, yj, sidj)
        env.placement[cj] = (xi, yi, sidi)
        limit -= 1
    # Global acceptance check: compute exact delta only on nets touching this batch
    nets_all = nets_map_from_graph_df(netlist_graph)
    fixed_all = fixed_points_from_pins(pins_df)
    def _hpwl_subset_with_positions(nets_subset: Set[int], use_env_positions: bool) -> float:
        total = 0.0
        for nb in nets_subset:
            cells = nets_all.get(nb, set())
            if not cells:
                continue
            xs: List[float] = []
            ys: List[float] = []
            for c in cells:
                if use_env_positions and c in env.placement:
                    x, y, _ = env.placement[c]
                    xs.append(float(x)); ys.append(float(y))
                elif c in placement_map:
                    x, y, _ = placement_map[c]
                    xs.append(float(x)); ys.append(float(y))
            for (fx, fy) in fixed_all.get(nb, []):
                xs.append(float(fx)); ys.append(float(fy))
            if len(xs) >= 2:
                total += (max(xs) - min(xs)) + (max(ys) - min(ys))
        return total

    # Snapshot original placements for this batch
    original_batch = {c: placement_map[c] for c in batch_cells}
    hpwl_global_before = _hpwl_subset_with_positions(nets_touch, use_env_positions=False)
    # Tentative commit in placement_map to reflect env outcome
    for c in batch_cells:
        placement_map[c] = env.placement[c]
    hpwl_after = hpwl_of_nets(env.nets, _pos_map(), env.fixed, net_subset=nets_touch)
    delta_local = hpwl_after - hpwl_before
    hpwl_global_after = _hpwl_subset_with_positions(nets_touch, use_env_positions=True)
    delta_global = hpwl_global_after - hpwl_global_before
    if delta_global > 0.0:
        # revert
        for c in batch_cells:
            placement_map[c] = original_batch[c]
        print(f"[SwapRefine] REVERT batch {len(batch_cells)}: local Δ={delta_local:.3f}, global Δ={delta_global:.3f} (worse).")
    else:
        print(f"[SwapRefine] COMMIT batch {len(batch_cells)}: local Δ={delta_local:.3f}, global Δ={delta_global:.3f} (improved/neutral).")
    return placement_map

# -------------------------
# Pipeline runner example
# -------------------------
def run_greedy_sa_then_rl_pipeline(fabric, fabric_df, pins_df, ports_df, netlist_graph,
                                   max_action_full: int = 1024,
                                   full_placer_train_eps: int = 100,
                                   swap_refine_train_eps: int = 200,
                                   batch_size: int = 64,
                                   device: str = "cpu",
                                   max_train_batches: int | None = None,
                                   max_apply_batches: int | None = None,
                                   full_steps_per_ep: int = 512,
                                   swap_steps_per_ep: int = 80,
                                   swap_bc_pretrain_epochs: int = 0,
                                   enable_timing: bool = False,
                                   full_log_csv: Optional[str] = None,
                                   swap_log_csv: Optional[str] = None,
                                   ppo_clip_eps: float = 0.2,
                                   ppo_value_coef: float = 1.0,
                                   ppo_entropy_coef: float = 0.01,
                                   ppo_max_grad_norm: float = 0.5):
    """
    1) Run Greedy+SA to get initial placement (calls your place_cells_greedy_sim_anneal).
    2) Train a small full-placer PPO (optionally) or use greedy to produce assignment order.
    3) Train swap refiner PPO on batches (selected by x-window).
    4) Apply swap refiner across batches to produce final placement_df.
    """
    # Import your functions here (local import to avoid top-level dependency)
    from src.placement.placer import place_cells_greedy_sim_anneal, assign_ports_to_pins, build_dependency_levels
    # Clean/semi-robust netlist: coerce net_bit to numeric and drop invalid rows
    if 'net_bit' in netlist_graph.columns:
        ng = netlist_graph.copy()
        ng['net_bit'] = pd.to_numeric(ng['net_bit'], errors='coerce')
        ng = ng.dropna(subset=['net_bit']).copy()
        ng['net_bit'] = ng['net_bit'].astype(int)
        ng['cell_name'] = ng['cell_name'].astype(str)
        netlist_graph = ng
    # 1) Greedy+SA
    t_total_start = time.perf_counter()
    t_greedy_start = time.perf_counter()
    updated_pins, placement_df = place_cells_greedy_sim_anneal(fabric, fabric_df, pins_df, ports_df, netlist_graph)
    t_greedy_end = time.perf_counter()
    # placement_df columns: cell_name, site_id, x_um, y_um
    sites_df = build_sites_from_fabric_df(fabric_df)
    sites_map = {int(r.site_id): (float(r.x_um), float(r.y_um)) for r in sites_df.itertuples(index=False)}
    fixed_pins = fixed_points_from_pins(updated_pins)
    t_level_start = time.perf_counter()
    g_levels = build_dependency_levels(updated_pins, netlist_graph)
    t_level_end = time.perf_counter()
    # build order by dependency level
    order = g_levels[["cell_name","dependency_level"]].drop_duplicates().sort_values(by=["dependency_level","cell_name"])
    cells_order = [str(x) for x in order["cell_name"].tolist()]

    # 0) Validate/repair directions if needed
    def _validate_and_enhance_directions(gdf: pd.DataFrame) -> pd.DataFrame:
        if 'direction' not in gdf.columns:
            return gdf
        df = gdf.copy()
        df['direction'] = df['direction'].astype(str).str.lower()
        if 'port' in df.columns and 'net_bit' in df.columns:
            for nb, grp in df.groupby('net_bit'):
                dirs = set(grp['direction'])
                if 'output' not in dirs and not grp.empty:
                    cand = grp[grp['port'].astype(str).str.upper().str.match(r'^(Y|Q|OUT)')]
                    idx = cand.index[0] if not cand.empty else grp.index[0]
                    df.at[idx, 'direction'] = 'output'
        return df

    netlist_graph = _validate_and_enhance_directions(netlist_graph)

    # 2) Full placer (optional): train/apply on curriculum windows preserving others via start_assignments
    t_full_train_total = 0.0
    if full_placer_train_eps and full_placer_train_eps > 0:
        # Build current placement map
        placement_map = {r.cell_name: (float(r.x_um), float(r.y_um), int(r.site_id)) for r in placement_df.itertuples(index=False)}
        window_sizes = [128, 256, 512]
        eps_per = max(1, full_placer_train_eps // len(window_sizes))
        full_agent = None
        assign_map = None
        for w in window_sizes:
            t_win_start = time.perf_counter()
            window_df = placement_df.sort_values(by=["x_um","y_um"]).head(w)
            window_cells = window_df["cell_name"].astype(str).tolist()
            start_assignments: Dict[str,int] = {c: int(placement_map[c][2]) for c in placement_map.keys() if c not in window_cells}
            window_order = [c for c in cells_order if c in set(window_cells)]
            env0 = build_full_assign_env_from_data(window_order, sites_df, netlist_graph, updated_pins, start_assignments=start_assignments, max_action=min(max_action_full, 32))
            obs0 = env0.reset(); obs_dim = obs0.shape[0]
            if full_agent is None:
                # Size actor head to the env max_action (kept constant across windows)
                full_agent = PPOAgent(obs_dim=obs_dim, action_dim=env0.max_action, device=device,
                                      clip_eps=ppo_clip_eps, value_coef=ppo_value_coef,
                                      entropy_coef=ppo_entropy_coef, max_grad_norm=ppo_max_grad_norm)
            train_ppo_full_placer(
                lambda: build_full_assign_env_from_data(window_order, sites_df, netlist_graph, updated_pins, start_assignments=start_assignments, max_action=min(max_action_full, 32)),
                full_agent,
                total_episodes=eps_per,
                steps_per_episode=full_steps_per_ep,
                device=device,
                log_csv_path=full_log_csv
            )
            assign_map = apply_full_placer_agent(full_agent, env0, eps=0.0)
            t_win_end = time.perf_counter()
            t_full_train_total += (t_win_end - t_win_start)
            if enable_timing:
                print(f"[RLTiming] full_placer_window size={w} eps={eps_per} time={t_win_end - t_win_start:.3f}s")
        if assign_map:
            rows = []
            for r in placement_df.itertuples(index=False):
                cname = str(r.cell_name)
                if cname in assign_map:
                    sid = int(assign_map[cname]); x,y = sites_map[sid]
                    rows.append({"cell_name": cname, "site_id": sid, "x_um": x, "y_um": y})
                else:
                    rows.append({"cell_name": cname, "site_id": int(r.site_id), "x_um": float(r.x_um), "y_um": float(r.y_um)})
            placement_df = pd.DataFrame(rows)

    # 3) Prepare batches for swap refiner training (hotspots + overlapping x-window)
    def selector(df: pd.DataFrame, i: int) -> List[str]:
        if df.empty:
            return []
        df_sorted = df.sort_values(by=["x_um","y_um"]).reset_index(drop=True)
        stride = max(1, batch_size // 2)
        start = i * stride
        end = start + batch_size
        if start >= len(df_sorted):
            return []
        return df_sorted.iloc[start:min(end, len(df_sorted))]["cell_name"].astype(str).tolist()

    nets_map = nets_map_from_graph_df(netlist_graph)
    placement_map: Dict[str, Tuple[float, float, int]] = {
        str(r.cell_name): (float(r.x_um), float(r.y_um), int(r.site_id))
        for r in placement_df.itertuples()
    }
    train_batches: List[Tuple[List[str], Dict[str, Tuple[float,float,int]], Dict[int, Tuple[float,float]], Dict[int, Set[str]], Dict[int, List[Tuple[float,float]]]]] = []

    # Hotspot-driven batches by current net HPWL
    pos_cells: Dict[str, Tuple[float, float]] = {
        str(r.cell_name): (float(r.x_um), float(r.y_um))
        for r in placement_df.itertuples(index=False)
    }
    fixed_map = fixed_points_from_pins(updated_pins)
    nets_hpwl: List[Tuple[int, float]] = []
    for nb, cs in nets_map.items():
        nets_hpwl.append((nb, hpwl_of_nets({nb: cs}, pos_cells, fixed_map, net_subset={nb})))
    nets_hpwl.sort(key=lambda x: x[1], reverse=True)

    hotspot_cells: List[str] = []
    for nb, _ in nets_hpwl[:min(200, len(nets_hpwl))]:
        hotspot_cells.extend(list(nets_map.get(nb, set())))
    seen: Set[str] = set()
    hotspot_cells = [c for c in hotspot_cells if not (c in seen or seen.add(c))]
    for i in range(0, len(hotspot_cells), max(1, batch_size // 2)):
        cells = hotspot_cells[i:i+batch_size]
        # enforce constant batch size for stable PPO backbone
        if len(cells) != batch_size:
            continue
        placement_subset = {c: placement_map[c] for c in cells if c in placement_map}
        if len(placement_subset) != batch_size:
            continue
        batch_site_ids = set(int(placement_map[c][2]) for c in placement_subset.keys())
        sites_local = {sid: sites_map[sid] for sid in batch_site_ids}
        nets_local: Dict[int, Set[str]] = {}
        fixed_local: Dict[int, List[Tuple[float,float]]] = {}
        for nb, cs in nets_map.items():
            if cs & set(cells):
                nets_local[nb] = cs
                if nb in fixed_pins:
                    fixed_local[nb] = fixed_pins[nb]
        train_batches.append((cells, placement_subset, sites_local, nets_local, fixed_local))

    # Overlapping x-window batches
    max_windows = max(1, len(placement_df) // max(1, batch_size // 2))
    for bidx in range(0, min(200, max_windows)):
        cells = selector(placement_df, bidx)
        if not cells or len(cells) != batch_size:
            continue
        placement_subset = {c: placement_map[c] for c in cells}
        if len(placement_subset) != batch_size:
            continue
        batch_site_ids = set(int(placement_map[c][2]) for c in cells)
        sites_local = {sid: sites_map[sid] for sid in batch_site_ids}
        nets_local = {}
        fixed_local = {}
        for nb, cs in nets_map.items():
            if cs & set(cells):
                nets_local[nb] = cs
                if nb in fixed_pins:
                    fixed_local[nb] = fixed_pins[nb]
        train_batches.append((cells, placement_subset, sites_local, nets_local, fixed_local))

    if not train_batches:
        print("No training batches found; returning Greedy+SA placement.")
        return placement_df

    # Build a swap agent sized by first batch
    # filter again in case earlier logic produced variable sizes
    train_batches = [tb for tb in train_batches if len(tb[0]) == batch_size]
    if not train_batches:
        print("No constant-size training batches; aborting swap refinement.")
        return placement_df
    first = train_batches[0]
    batch_cells, placement_subset, sites_local, nets_local, fixed_local = first
    # Build global type maps
    site_types_map_full: Dict[int,str] = {}
    if 'cell_type' in sites_df.columns:
        site_types_map_full = {int(r.site_id): str(getattr(r,'cell_type')) for r in sites_df.itertuples(index=False) if hasattr(r,'cell_type')}
    cell_types_map_full: Dict[str,str] = {}
    if 'cell_type' in netlist_graph.columns:
        cell_types_map_full = {str(r.cell_name): str(getattr(r,'cell_type')) for r in netlist_graph[['cell_name','cell_type']].dropna().itertuples(index=False)}
    site_types_local = {sid: site_types_map_full.get(sid, '') for sid in sites_local.keys()}
    cell_types_local = {c: cell_types_map_full.get(c, '') for c in batch_cells}
    env0 = SwapRefineEnv(batch_cells, placement_subset, sites_local, nets_local, fixed_local,
                         site_types_map=site_types_local, cell_types_map=cell_types_local)
    obs0 = env0.reset()
    obs_dim_swap = obs0.shape[0]
    action_dim_swap = len(env0.action_pairs)
    swap_agent = PPOAgent(obs_dim=obs_dim_swap, action_dim=action_dim_swap, device=device,
                          clip_eps=ppo_clip_eps, value_coef=ppo_value_coef,
                          entropy_coef=ppo_entropy_coef, max_grad_norm=ppo_max_grad_norm)

    # Optional: BC pretrain for swap agent to warm start
    if swap_bc_pretrain_epochs and swap_bc_pretrain_epochs > 0:
        # use first batch distribution for labeling, repeated per epoch inside the function
        env_builder = swap_env_builder_factory(first)
        pretrain_bc_swap_refiner(env_builder, swap_agent, epochs=swap_bc_pretrain_epochs, steps_per_episode=swap_steps_per_ep, device=device)

    # Train swap agent (PPO)
    def swap_env_builder_factory(batch_tuple):
        def _fn():
            cells_b, placement_subset_b, sites_local_b, nets_local_b, fixed_local_b = batch_tuple
            site_types_local_b = {sid: site_types_map_full.get(sid, '') for sid in sites_local_b.keys()}
            cell_types_local_b = {c: cell_types_map_full.get(c, '') for c in cells_b}
            return SwapRefineEnv(cells_b, placement_subset_b, sites_local_b, nets_local_b, fixed_local_b,
                                 site_types_map=site_types_local_b, cell_types_map=cell_types_local_b)
        return _fn

    # sample some batches for training
    limit_train = min(len(train_batches), max_train_batches if max_train_batches is not None else 50)
    sample_batches = train_batches[:limit_train]
    # train agent on each batch sequentially (quick prototype)
    t_swap_train_total = 0.0
    for bidx, b in enumerate(sample_batches):
        t_batch_start = time.perf_counter()
        env_builder = swap_env_builder_factory(b)
        train_ppo_swap_refiner(env_builder, swap_agent, episodes=swap_refine_train_eps, steps_per_episode=swap_steps_per_ep, device=device, log_csv_path=swap_log_csv)
        t_batch_end = time.perf_counter()
        t_swap_train_total += (t_batch_end - t_batch_start)
        if enable_timing:
            print(f"[RLTiming] swap_train_batch index={bidx} time={t_batch_end - t_batch_start:.3f}s")

    # 4) Apply swap refiner across all batches
    total_apply = len(placement_df)//batch_size
    if max_apply_batches is not None:
        total_apply = min(total_apply, max_apply_batches)
    t_swap_apply_total = 0.0
    for bidx in range(0, total_apply):
        t_apply_start = time.perf_counter()
        cells = selector(placement_df, bidx)
        if not cells or len(cells) != batch_size:
            continue
        placement_map = apply_swap_refiner(swap_agent, cells, placement_map, sites_map, netlist_graph, updated_pins, steps=50,
                           site_types_map=site_types_map_full, cell_types_map=cell_types_map_full)
        t_apply_end = time.perf_counter()
        t_swap_apply_total += (t_apply_end - t_apply_start)
        if enable_timing:
            print(f"[RLTiming] swap_apply_batch index={bidx} time={t_apply_end - t_apply_start:.3f}s")

    # rebuild placement_df from placement_map
    rows = []
    for cell_name, (x,y,sid) in placement_map.items():
        rows.append({"cell_name": cell_name, "site_id": int(sid), "x_um": float(x), "y_um": float(y)})
    refined_df = pd.DataFrame(rows)
    t_total_end = time.perf_counter()
    if enable_timing:
        print(f"[RLTiming] summary total={t_total_end - t_total_start:.3f}s greedy_sa={t_greedy_end - t_greedy_start:.3f}s levelize={t_level_end - t_level_start:.3f}s full_train={t_full_train_total:.3f}s swap_train={t_swap_train_total:.3f}s swap_apply={t_swap_apply_total:.3f}s")
    return refined_df

# Simple note when executed directly
if __name__ == "__main__":
    import sys
    if "--smoke-cell-type" in sys.argv:
        # Simple smoke test for FullAssignEnv cell type filtering
        # Create 6 sites: 3 of type A, 3 of type B
        sites_df_test = pd.DataFrame({
            "cell_x": [0,10,20, 0,10,20],
            "cell_y": [0,0,0, 10,10,10],
            "cell_type": ["A","A","A","B","B","B"],
        })
        sites_df_test = build_sites_from_fabric_df(sites_df_test)
        # Define cells: 4 cells of type A, 2 of type B
        cells_order = ["cA1","cA2","cA3","cA4","cB1","cB2"]
        netlist_graph_test = pd.DataFrame({
            "cell_name": cells_order,
            "net_bit": [0,1,2,3,4,5],
            "cell_type": ["A","A","A","A","B","B"],
        })
        pins_df_test = pd.DataFrame({"net_bit": [], "x_um": [], "y_um": []})
        env = build_full_assign_env_from_data(cells_order, sites_df_test, netlist_graph_test, pins_df_test, max_action=10)
        obs = env.reset()
        # After reset, candidates for first cell (type A) should only include A sites
        cand_ids = env._last_candidates
        cand_site_types = [env.site_types[idx] if env.site_types else "?" for idx in cand_ids]
        print("[SmokeTest] Candidate site types for first A cell:", cand_site_types)
        if all(ct == "A" for ct in cand_site_types):
            print("[SmokeTest] PASS: filtering restricts to matching site types.")
        else:
            print("[SmokeTest] FAIL: unexpected site type in candidates.")
    else:
        print("This module provides PPO-based placer/refiner helpers. Import and call run_greedy_sa_then_rl_pipeline(...) from your driver script.")

# End of file
