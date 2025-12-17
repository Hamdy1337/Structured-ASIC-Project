#!/usr/bin/env python3
"""
knob_sweep_parallel.py

Parallel one-at-a-time knob sweep for SA placer.

Knobs supported:
  - T_initial: includes 1000 plus samples in [1e6, 1e8] (log/lin/random)
  - cooling_rate: [0.8, 0.999]
  - batch_size: [10, 1000]
  - moves_per_temp: user-provided list (default included)

Runs experiments in parallel (10–20 workers recommended) while holding all other knobs fixed.
Outputs a CSV with runtime + HPWL and marks Pareto-dominated points.

Example:
  python knob_sweep_parallel.py --design-json inputs/designs/6502_mapped.json \
      --workers 16 --runs-per-setting 1 --out build/knob_parallel_6502.csv

"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path to allow 'src' imports
sys.path.append(str(Path(__file__).resolve().parents[2]))

import argparse
import time
import math
import traceback
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set, Any

import numpy as np
import pandas as pd

# Your project imports
from src.parsers.fabric_db import get_fabric_db
from src.parsers.pins_parser import load_and_validate
from src.parsers.netlist_parser import parse_netlist
from src.placement.placer import place_cells_greedy_sim_anneal
from src.placement.placement_utils import nets_by_cell, fixed_points_from_pins, hpwl_for_nets


# -----------------------------
# Worker globals (loaded once per process)
# -----------------------------
_G: Dict[str, Any] = {}


def _init_worker(fabric_yaml: str, fabric_cells_yaml: str, pins_yaml: str, design_json: str) -> None:
    """
    Initializer for each process: load heavy inputs once.
    Note: On macOS (spawn), each worker is a fresh process, so this is worth doing.
    """
    global _G
    fabric, fabric_df = get_fabric_db(fabric_yaml, fabric_cells_yaml)
    pins_df, _pins_meta = load_and_validate(pins_yaml)
    logical_db, ports_df, netlist_graph = parse_netlist(design_json)

    _G = {
        "fabric": fabric,
        "fabric_df": fabric_df,
        "pins_df": pins_df,
        "ports_df": ports_df,
        "netlist_graph": netlist_graph,
        "design_stem": Path(design_json).stem.replace("_mapped", ""),
    }


def _compute_global_hpwl(placement_df: pd.DataFrame,
                         updated_pins: pd.DataFrame,
                         netlist_graph: pd.DataFrame) -> float:
    """Compute total HPWL for the placement."""
    pos_cells: Dict[str, Tuple[float, float]] = {
        str(r.cell_name): (float(r.x_um), float(r.y_um))
        for r in placement_df.itertuples(index=False)
    }
    cell_to_nets = nets_by_cell(netlist_graph)
    fixed_pts = fixed_points_from_pins(updated_pins)
    all_nets: Set[int] = set()
    for nets in cell_to_nets.values():
        all_nets |= nets
    return hpwl_for_nets(all_nets, pos_cells, cell_to_nets, fixed_pts)


def _pareto_flags(rows: List[Dict[str, Any]]) -> List[int]:
    """Mark dominated rows (1 = dominated, 0 = non-dominated)."""
    flags: List[int] = []
    for i, a in enumerate(rows):
        dominated = 0
        for j, b in enumerate(rows):
            if i == j:
                continue
            if (b["runtime_sec"] <= a["runtime_sec"] and b["hpwl"] <= a["hpwl"] and
                (b["runtime_sec"] < a["runtime_sec"] or b["hpwl"] < a["hpwl"])):
                dominated = 1
                break
        flags.append(dominated)
    return flags


def _parse_temp(t_raw: str) -> Optional[float]:
    """Parse temperature string to float or None."""
    if str(t_raw).lower() == "auto":
        return None
    try:
        return float(t_raw)
    except ValueError:
        return None


def _run_one(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Worker function: runs one experiment.
    Uses globals loaded in _init_worker.
    """
    global _G
    try:
        knob_name = task["knob_name"]
        knob_value = task["knob_value"]
        run_seed = int(task["seed"])

        # Fixed baseline knobs
        cooling_rate = float(task["cooling_rate"])
        moves_per_temp = int(task["moves_per_temp"])
        p_refine = float(task["p_refine"])
        p_explore = float(task["p_explore"])
        refine_max_distance = float(task["refine_max_distance"])
        W_initial = float(task["W_initial"])
        batch_size = int(task["batch_size"])
        T_initial_raw = str(task["T_initial_raw"])

        if p_refine + p_explore <= 0:
            return None

        t_start = time.perf_counter()
        t_initial_val = _parse_temp(T_initial_raw)

        updated_pins, placement_df, _val, _sa_hpwl = place_cells_greedy_sim_anneal(
            _G["fabric"],
            _G["fabric_df"],
            _G["pins_df"],
            _G["ports_df"],
            _G["netlist_graph"],
            sa_moves_per_temp=moves_per_temp,
            sa_cooling_rate=cooling_rate,
            sa_T_initial=t_initial_val,
            sa_p_refine=p_refine,
            sa_p_explore=p_explore,
            sa_refine_max_distance=refine_max_distance,
            sa_W_initial=W_initial,
            sa_seed=run_seed,
            sa_batch_size=batch_size,
        )

        t_end = time.perf_counter()
        hpwl_val = _compute_global_hpwl(placement_df, updated_pins, _G["netlist_graph"])
        runtime = t_end - t_start

        return {
            "design": _G["design_stem"],
            "knob_name": knob_name,
            "knob_value": knob_value,
            "cooling_rate": cooling_rate,
            "moves_per_temp": moves_per_temp,
            "p_refine": p_refine,
            "p_explore": p_explore,
            "refine_max_distance": refine_max_distance,
            "W_initial": W_initial,
            "T_initial_raw": T_initial_raw,
            "batch_size": batch_size,
            "runtime_sec": runtime,
            "hpwl": hpwl_val,
            "seed": run_seed,
        }

    except Exception as e:
        print(f"[KNOB] ERROR task={task.get('knob_name')} value={task.get('knob_value')}: {e}")
        print(traceback.format_exc())
        return None


def _make_list_from_csv_ints(s: str) -> List[int]:
    vals = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        vals.append(int(part))
    return vals


def _make_t_initial_values(mode: str, n: int, lo: float, hi: float, include_1000: bool, rng: np.random.Generator) -> List[float]:
    """
    Build a T_initial sweep list.
      - include_1000: always adds 1000 first (your request)
      - then adds n values in [lo, hi] by mode: log, lin, random
    """
    vals: List[float] = []
    if include_1000:
        vals.append(1000.0)

    if n <= 0:
        return vals

    if mode == "log":
        rest = np.logspace(math.log10(lo), math.log10(hi), n).tolist()
    elif mode == "lin":
        rest = np.linspace(lo, hi, n).tolist()
    elif mode == "random":
        # log-uniform random is usually better for huge ranges
        # sample u~U(log10(lo),log10(hi)), take 10^u
        u = rng.uniform(math.log10(lo), math.log10(hi), n)
        rest = (10 ** u).tolist()
    else:
        raise ValueError(f"Unknown T_initial mode: {mode}")

    vals.extend([float(round(x, 6)) for x in rest])
    return vals


def build_tasks(design_json: str,
                runs_per_setting: int,
                base_seed: int,
                # Baseline knobs
                base_cooling_rate: float,
                base_moves_per_temp: int,
                base_p_refine: float,
                base_p_explore: float,
                base_refine_max_distance: float,
                base_W_initial: float,
                base_T_initial_raw: str,
                base_batch_size: int,
                # Sweep lists
                t_initial_values: List[float],
                cooling_rates: List[float],
                batch_sizes: List[int],
                moves_list: List[int]) -> List[Dict[str, Any]]:
    """
    Create one-at-a-time sweep tasks:
      - Sweep T_initial (others baseline)
      - Sweep cooling_rate
      - Sweep batch_size
      - Sweep moves_per_temp
    """
    tasks: List[Dict[str, Any]] = []
    setting_id = 0

    def add_tasks_for_knob(knob_name: str, values: List[Any], apply_fn):
        nonlocal setting_id
        for v in values:
            for rep in range(runs_per_setting):
                # Deterministic unique seed per setting+rep:
                run_seed = base_seed + (setting_id * 1000) + rep
                setting_id += 1

                task = {
                    "design_json": design_json,
                    "knob_name": knob_name,
                    "knob_value": v,
                    "seed": run_seed,

                    # Baselines (will be overridden by apply_fn for this knob)
                    "cooling_rate": base_cooling_rate,
                    "moves_per_temp": base_moves_per_temp,
                    "p_refine": base_p_refine,
                    "p_explore": base_p_explore,
                    "refine_max_distance": base_refine_max_distance,
                    "W_initial": base_W_initial,
                    "T_initial_raw": base_T_initial_raw,
                    "batch_size": base_batch_size,
                }

                apply_fn(task, v)
                tasks.append(task)

    # Sweep T_initial
    add_tasks_for_knob(
        "T_initial",
        t_initial_values,
        lambda task, v: task.update({"T_initial_raw": str(v)})
    )

    # Sweep cooling_rate
    add_tasks_for_knob(
        "cooling_rate",
        cooling_rates,
        lambda task, v: task.update({"cooling_rate": float(v)})
    )

    # Sweep batch_size
    add_tasks_for_knob(
        "batch_size",
        batch_sizes,
        lambda task, v: task.update({"batch_size": int(v)})
    )

    # Sweep moves_per_temp
    add_tasks_for_knob(
        "moves_per_temp",
        moves_list,
        lambda task, v: task.update({"moves_per_temp": int(v)})
    )

    return tasks


def main() -> None:
    ap = argparse.ArgumentParser(description="Parallel one-at-a-time knob sweep for SA placer")

    ap.add_argument("--design-json", default="inputs/designs/6502_mapped.json")
    ap.add_argument("--out", default="build/knob_parallel_results.csv")
    ap.add_argument("--workers", type=int, default=12, help="Parallel workers (try 10–20)")
    ap.add_argument("--runs-per-setting", type=int, default=1, help="Repeats per knob value with different seeds")
    ap.add_argument("--seed", type=int, default=42)

    # Input files (adjust if your paths differ)
    ap.add_argument("--fabric-yaml", default="inputs/Platform/fabric.yaml")
    ap.add_argument("--fabric-cells-yaml", default="inputs/Platform/fabric_cells.yaml")
    ap.add_argument("--pins-yaml", default="inputs/Platform/pins.yaml")

    # Baseline knobs (held constant unless swept)
    ap.add_argument("--base-cooling-rate", type=float, default=0.95)
    ap.add_argument("--base-moves-per-temp", type=int, default=1000)
    ap.add_argument("--base-p-refine", type=float, default=0.7)
    ap.add_argument("--base-p-explore", type=float, default=0.3)
    ap.add_argument("--base-refine-max-distance", type=float, default=100.0)
    ap.add_argument("--base-W-initial", type=float, default=0.5)
    ap.add_argument("--base-T-initial", default="auto")
    ap.add_argument("--base-batch-size", type=int, default=200)

    # Sweep config: T_initial
    ap.add_argument("--t-mode", choices=["log", "lin", "random"], default="log",
                    help="How to sample T_initial in [t-lo, t-hi]")
    ap.add_argument("--t-n", type=int, default=10, help="How many values to generate in [t-lo, t-hi] (in addition to 1000)")
    ap.add_argument("--t-lo", type=float, default=1e6)
    ap.add_argument("--t-hi", type=float, default=1e8)
    ap.add_argument("--t-include-1000", action="store_true", default=True)

    # Sweep config: cooling_rate
    ap.add_argument("--cooling-n", type=int, default=10)
    ap.add_argument("--cooling-lo", type=float, default=0.8)
    ap.add_argument("--cooling-hi", type=float, default=0.999)

    # Sweep config: batch_size
    ap.add_argument("--batch-lo", type=int, default=10)
    ap.add_argument("--batch-hi", type=int, default=1000)
    ap.add_argument("--batch-n", type=int, default=10)

    # Sweep config: moves_per_temp (you said undecided -> provide default but allow override)
    ap.add_argument("--moves-list", default="200,400,600,800,1000,1200,1400,1600,1800,2000",
                    help="Comma-separated list of moves_per_temp values")

    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)

    # Build sweep lists
    t_initial_values = _make_t_initial_values(
        mode=args.t_mode,
        n=args.t_n,
        lo=args.t_lo,
        hi=args.t_hi,
        include_1000=args.t_include_1000,
        rng=rng,
    )

    cooling_rates = np.linspace(args.cooling_lo, args.cooling_hi, args.cooling_n).tolist()
    batch_sizes = np.linspace(args.batch_lo, args.batch_hi, args.batch_n).round().astype(int).tolist()
    moves_list = _make_list_from_csv_ints(args.moves_list)

    print("=" * 80)
    print("Parallel One-at-a-Time Knob Sweep")
    print("=" * 80)
    print(f"Design:            {args.design_json}")
    print(f"Workers:           {args.workers}")
    print(f"Runs/setting:      {args.runs_per_setting}")
    print(f"Output:            {out_path}")
    print("-" * 80)
    print("Baselines:")
    print(f"  cooling_rate     = {args.base_cooling_rate}")
    print(f"  moves_per_temp   = {args.base_moves_per_temp}")
    print(f"  p_refine/explore = {args.base_p_refine}/{args.base_p_explore}")
    print(f"  refine_max_dist  = {args.base_refine_max_distance}")
    print(f"  W_initial        = {args.base_W_initial}")
    print(f"  T_initial        = {args.base_T_initial}")
    print(f"  batch_size       = {args.base_batch_size}")
    print("-" * 80)
    print("Sweep sizes:")
    print(f"  T_initial        : {len(t_initial_values)} values (includes 1000 + {args.t_n} in [{args.t_lo:.2g}, {args.t_hi:.2g}] via {args.t_mode})")
    print(f"  cooling_rate     : {len(cooling_rates)} values in [{args.cooling_lo}, {args.cooling_hi}]")
    print(f"  batch_size       : {len(batch_sizes)} values in [{args.batch_lo}, {args.batch_hi}]")
    print(f"  moves_per_temp   : {len(moves_list)} values")
    print("=" * 80)

    tasks = build_tasks(
        design_json=args.design_json,
        runs_per_setting=args.runs_per_setting,
        base_seed=args.seed,
        base_cooling_rate=args.base_cooling_rate,
        base_moves_per_temp=args.base_moves_per_temp,
        base_p_refine=args.base_p_refine,
        base_p_explore=args.base_p_explore,
        base_refine_max_distance=args.base_refine_max_distance,
        base_W_initial=args.base_W_initial,
        base_T_initial_raw=args.base_T_initial,
        base_batch_size=args.base_batch_size,
        t_initial_values=t_initial_values,
        cooling_rates=cooling_rates,
        batch_sizes=batch_sizes,
        moves_list=moves_list,
    )

    print(f"[KNOB] Total tasks: {len(tasks)}")
    print("[KNOB] Starting parallel execution...\n")

    # Parallel execution
    from concurrent.futures import ProcessPoolExecutor, as_completed

    results: List[Dict[str, Any]] = []
    t0 = time.perf_counter()

    with ProcessPoolExecutor(
        max_workers=args.workers,
        initializer=_init_worker,
        initargs=(args.fabric_yaml, args.fabric_cells_yaml, args.pins_yaml, args.design_json),
    ) as ex:
        futs = [ex.submit(_run_one, task) for task in tasks]

        done = 0
        first_write = True
        
        # Ensure output directory exists
        out_path.parent.mkdir(parents=True, exist_ok=True)

        for fut in as_completed(futs):
            done += 1
            r = fut.result()
            if r is not None:
                results.append(r)
                
                # Incremental save
                df_chunk = pd.DataFrame([r])
                # Write header only on first write; append ('a') thereafter
                mode = 'w' if first_write else 'a'
                header = first_write
                df_chunk.to_csv(out_path, mode=mode, header=header, index=False)
                first_write = False

            if done % max(1, len(futs)//20) == 0:
                print(f"[KNOB] Progress: {done}/{len(futs)} done, successes={len(results)}")

    t1 = time.perf_counter()
    print(f"\n[KNOB] Finished. Wall time: {t1 - t0:.2f} sec. Successes: {len(results)}/{len(tasks)}")

    if not results:
        print("[KNOB] No successful runs. Check logs above.")
        return

    # Pareto
    flags = _pareto_flags(results)
    for row, flag in zip(results, flags):
        row["dominated"] = flag

    df = pd.DataFrame(results)
    # We already wrote the CSV, but let's re-write it at the end to include 'dominated' column
    df.to_csv(out_path, index=False)
    print(f"[KNOB] Final CSV finalized: {out_path}")

    # Quick summaries
    print("\n" + "=" * 80)
    print("Best per knob (min HPWL):")
    print("=" * 80)
    for knob in sorted(df["knob_name"].unique()):
        sub = df[df["knob_name"] == knob]
        best = sub.loc[sub["hpwl"].idxmin()]
        print(f"  {knob:14s} best={best['knob_value']}  hpwl={best['hpwl']:.2f}  runtime={best['runtime_sec']:.3f}s")

    pareto_df = df[df["dominated"] == 0].sort_values(["runtime_sec", "hpwl"])
    print("\n" + "=" * 80)
    print("Pareto (non-dominated):")
    print("=" * 80)
    for _, r in pareto_df.iterrows():
        print(f"  {r.knob_name}={r.knob_value}  runtime={r.runtime_sec:.3f}s  hpwl={r.hpwl:.2f}")

    print("\n[KNOB] Done.")


if __name__ == "__main__":
    main()


"""
python3 src/experiments/knob_sweep_parallel.py \
  --design-json inputs/designs/6502_mapped.json \
  --workers 4 \
  --runs-per-setting 1 \
  --t-mode log \
  --cooling-hi 0.98 \
  --batch-lo 200 \
  --base-moves-per-temp 200 \
  --moves-list "100,200,400,600,800" \
  --out build/knob_parallel_6502_v3.csv


  python3 src/experiments/visualize_knob_csv.py --csv build/knob_parallel_6502_v3.csv --out-dir build/plots_v3
"""