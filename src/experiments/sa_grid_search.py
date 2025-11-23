"""
sa_grid_search.py: Run grid search experiments over Simulated Annealing (SA) placer knobs
for a single design (default: 6502) and produce a runtime vs HPWL scatter plot.

Extended Parameters Supported (each can take multiple values):
    --cooling-list            (alpha cooling rate per temperature shrink)
    --moves-list              (moves per temperature block)
    --refine-prob-list        (p_refine, probability of local/nearby refine move)
    --explore-prob-list       (p_explore, probability of broader explore move)
    --refine-dist-list        (refine_max_distance in microns for refine move)
    --win-init-list           (W_initial fraction of die size for initial explore window)
    --temp-initial-list       (Initial temperature values; use 'auto' to let placer compute)

Usage example (PowerShell):
        python -m src.experiments.sa_grid_search `
                --design-json inputs/designs/6502_mapped.json `
                --cooling-list 0.80 0.90 0.95 `
                --moves-list 100 200 `
                --refine-prob-list 0.7 0.6 `
                --explore-prob-list 0.3 0.4 `
                --refine-dist-list 50 100 `
                --win-init-list 0.5 0.3 `
                --temp-initial-list auto 50 100 `
                --runs-per-setting 1 `
                --out-prefix build/sa_6502_ext

This will generate:
    - CSV: build/sa_6502_ext.results.csv
    - Scatter plot: build/sa_6502_ext.scatter.png
    - Annotated Pareto front in the same scatter plot

Columns in CSV:
    design, cooling_rate, moves_per_temp, p_refine, p_explore, refine_max_distance,
    W_initial, T_initial_raw, runtime_sec, hpwl, seed, dominated (0/1)

Pareto dominance criterion:
    A setting A dominates B if runtime_A <= runtime_B and hpwl_A <= hpwl_B
    with at least one strict inequality.

You can add results & plot into README.md and write analysis of the trade-off.
"""
from __future__ import annotations

import argparse
import time
import random
from pathlib import Path
from typing import List, Tuple, Dict, Set
import math
import csv

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.parsers.fabric_db import get_fabric_db
from src.parsers.pins_parser import load_and_validate
from src.parsers.netlist_parser import parse_netlist
from src.placement.placer import place_cells_greedy_sim_anneal
from src.placement.placement_utils import nets_by_cell, fixed_points_from_pins, hpwl_for_nets, in_out_nets_by_cell


def _compute_global_hpwl(placement_df: pd.DataFrame,
                         updated_pins: pd.DataFrame,
                         netlist_graph: pd.DataFrame) -> float:
    # Build pos map
    pos_cells: Dict[str, Tuple[float, float]] = {
        str(r.cell_name): (float(r.x_um), float(r.y_um))
        for r in placement_df.itertuples(index=False)
    }
    cell_to_nets = nets_by_cell(netlist_graph)
    fixed_pts = fixed_points_from_pins(updated_pins)
    # Union of all nets used by cells
    all_nets: Set[int] = set()
    for nets in cell_to_nets.values():
        all_nets |= nets
    return hpwl_for_nets(all_nets, pos_cells, cell_to_nets, fixed_pts)


def _pareto_flags(rows: List[Dict[str, float]]) -> List[int]:
    # Mark dominated rows (1 = dominated, 0 = non-dominated)
    # A dominates B if runtime <= and hpwl <= with at least one strict.
    flags = []
    for i, a in enumerate(rows):
        dominated = 0
        for j, b in enumerate(rows):
            if i == j:
                continue
            if (b['runtime_sec'] <= a['runtime_sec'] and b['hpwl'] <= a['hpwl'] and
                    (b['runtime_sec'] < a['runtime_sec'] or b['hpwl'] < a['hpwl'])):
                dominated = 1
                break
        flags.append(dominated)
    return flags


def run_grid_search(design_json: str,
                     cooling_list: List[float],
                     moves_list: List[int],
                     refine_prob_list: List[float],
                     explore_prob_list: List[float],
                     refine_dist_list: List[float],
                     win_init_list: List[float],
                     temp_initial_list: List[str],
                     runs_per_setting: int,
                     seed: int,
                     out_prefix: Path) -> pd.DataFrame:
    # Load shared inputs once
    fabric, fabric_df = get_fabric_db("inputs/Platform/fabric.yaml", "inputs/Platform/fabric_cells.yaml")
    pins_df, _pins_meta = load_and_validate("inputs/Platform/pins.yaml")
    logical_db, ports_df, netlist_graph = parse_netlist(design_json)

    results: List[Dict[str, float]] = []
    setting_id = 0
    for cooling_rate in cooling_list:
        for moves_per_temp in moves_list:
            for p_refine in refine_prob_list:
                for p_explore in explore_prob_list:
                    if p_refine + p_explore <= 0:
                        continue  # skip invalid probability combo
                    for refine_dist in refine_dist_list:
                        for win_init in win_init_list:
                            for t_init_raw in temp_initial_list:
                                for run_idx in range(runs_per_setting):
                                    setting_id += 1
                                    run_seed = seed + setting_id  # unique seed per run
                                    print(f"[GRID] Setting {setting_id}: alpha={cooling_rate} moves={moves_per_temp} p_refine={p_refine} p_explore={p_explore} dist={refine_dist} win_init={win_init} T0={t_init_raw} seed={run_seed}")
                                    t_start = time.perf_counter()
                                    # Interpret initial temperature
                                    if str(t_init_raw).lower() == 'auto':
                                        t_initial_val = None
                                    else:
                                        try:
                                            t_initial_val = float(t_init_raw)
                                        except ValueError:
                                            t_initial_val = None
                                    # Run placer
                                    updated_pins, placement_df, _val = place_cells_greedy_sim_anneal(
                                        fabric,
                                        fabric_df,
                                        pins_df,
                                        ports_df,
                                        netlist_graph,
                                        sa_moves_per_temp=moves_per_temp,
                                        sa_cooling_rate=cooling_rate,
                                        sa_T_initial=t_initial_val,
                                        sa_p_refine=p_refine,
                                        sa_p_explore=p_explore,
                                        sa_refine_max_distance=refine_dist,
                                        sa_W_initial=win_init,
                                        sa_seed=run_seed,
                                    )
                                    t_end = time.perf_counter()
                                    hpwl_val = _compute_global_hpwl(placement_df, updated_pins, netlist_graph)
                                    runtime = t_end - t_start
                                    results.append({
                                        'design': Path(design_json).stem.replace('_mapped', ''),
                                        'cooling_rate': cooling_rate,
                                        'moves_per_temp': moves_per_temp,
                                        'p_refine': p_refine,
                                        'p_explore': p_explore,
                                        'refine_max_distance': refine_dist,
                                        'W_initial': win_init,
                                        'T_initial_raw': t_init_raw,
                                        'runtime_sec': runtime,
                                        'hpwl': hpwl_val,
                                        'seed': run_seed,
                                    })
    # Pareto flagging
    flags = _pareto_flags(results)
    for row, flag in zip(results, flags):
        row['dominated'] = flag
    df = pd.DataFrame(results)
    return df


def plot_runtime_hpwl(df: pd.DataFrame, out_prefix: Path) -> Path:
    plt.figure(figsize=(10, 7))
    dominated = df['dominated'] == 1
    non_dom = df['dominated'] == 0
    
    # Plot dominated points
    plt.scatter(df.loc[dominated, 'runtime_sec'], df.loc[dominated, 'hpwl'], 
                c='lightgray', alpha=0.5, s=30, label='Dominated', zorder=1)
    
    # Plot Pareto front points
    plt.scatter(df.loc[non_dom, 'runtime_sec'], df.loc[non_dom, 'hpwl'], 
                c='tab:blue', edgecolors='white', s=80, linewidth=1.5, label='Pareto Front', zorder=2)
    
    # Connect Pareto points with a line to visualize the front
    pareto_points = df.loc[non_dom].sort_values('runtime_sec')
    plt.plot(pareto_points['runtime_sec'], pareto_points['hpwl'], 
             c='tab:blue', linestyle='--', alpha=0.5, zorder=1)

    # Annotate only the most significant Pareto points (e.g., min runtime, min HPWL, and "knee" points)
    # Simple heuristic: annotate all Pareto points but with cleaner formatting
    for _, r in pareto_points.iterrows():
        label = f"α={r.cooling_rate:.2f}\nN={int(r.moves_per_temp)}"
        plt.annotate(label,
                     (r.runtime_sec, r.hpwl),
                     textcoords='offset points', 
                     xytext=(5, 5), 
                     fontsize=8,
                     bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.8),
                     zorder=3)

    plt.xlabel('Runtime (s)', fontsize=10)
    plt.ylabel('Final HPWL (um)', fontsize=10)
    plt.title(f"SA Placer Trade-off: Runtime vs Quality ({df['design'].iloc[0]})", fontsize=12)
    
    # Clean up grid and spines
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.gca().spines['top'].set_visible(False)
    plt.gca().spines['right'].set_visible(False)
    
    plt.legend(frameon=True, fancybox=True, framealpha=0.9)
    
    out_path = out_prefix.with_suffix('.scatter.png')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    print(f"[GRID] Saved scatter plot: {out_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description='Grid search SA placer knobs and plot runtime vs HPWL.')
    ap.add_argument('--design-json', default='inputs/designs/6502_mapped.json', help='Design mapped JSON path')
    ap.add_argument('--cooling-list', type=float, nargs='+', default=[0.80,0.85,0.90,0.95,0.99], help='Cooling rates (alpha values) to test')
    ap.add_argument('--moves-list', type=int, nargs='+', default=[50,100,150,200,300], help='Moves per temperature (sa_moves_per_temp) values to test')
    ap.add_argument('--refine-prob-list', type=float, nargs='+', default=[0.7], help='Refine move probabilities')
    ap.add_argument('--explore-prob-list', type=float, nargs='+', default=[0.3], help='Explore move probabilities')
    ap.add_argument('--refine-dist-list', type=float, nargs='+', default=[100.0], help='Refine max Manhattan distance list')
    ap.add_argument('--win-init-list', type=float, nargs='+', default=[0.5], help='Initial explore window fraction list')
    ap.add_argument('--temp-initial-list', nargs='+', default=['auto'], help="Initial temperature values (float) or 'auto'")
    ap.add_argument('--runs-per-setting', type=int, default=1, help='Repeat each setting this many times (different seeds)')
    ap.add_argument('--seed', type=int, default=42, help='Base seed for reproducibility')
    ap.add_argument('--out-prefix', default='build/sa_6502', help='Prefix for output CSV and plot')
    args = ap.parse_args()

    out_prefix = Path(args.out_prefix)
    df = run_grid_search(
        args.design_json,
        args.cooling_list,
        args.moves_list,
        args.refine_prob_list,
        args.explore_prob_list,
        args.refine_dist_list,
        args.win_init_list,
        args.temp_initial_list,
        args.runs_per_setting,
        args.seed,
        out_prefix,
    )

    # Save CSV
    csv_path = out_prefix.with_suffix('.results.csv')
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    print(f"[GRID] Saved results CSV: {csv_path}")

    # Plot
    plot_runtime_hpwl(df, out_prefix)

    # Print quick Pareto summary
    pareto_df = df[df['dominated'] == 0].sort_values(by=['runtime_sec'])
    print('\nPareto Front (non-dominated points):')
    for _, r in pareto_df.iterrows():
        print(f"  alpha={r.cooling_rate:.2f} moves={int(r.moves_per_temp)} p_ref={r.p_refine:.2f} p_exp={r.p_explore:.2f} dist={r.refine_max_distance:.1f} win={r.W_initial:.2f} T0={r.T_initial_raw} runtime={r.runtime_sec:.3f}s hpwl={r.hpwl:.2f}")

    # Recommend default: pick point with lowest hpwl among those within 1.2x min runtime
    min_runtime = df['runtime_sec'].min()
    candidate_df = df[df['runtime_sec'] <= 1.2 * min_runtime]
    if not candidate_df.empty:
        best_row = candidate_df.sort_values(by='hpwl').iloc[0]
        print('\nSuggested default setting (within 1.2x min runtime, lowest HPWL):')
        print(f"  alpha={best_row.cooling_rate:.2f} moves={int(best_row.moves_per_temp)} p_ref={best_row.p_refine:.2f} p_exp={best_row.p_explore:.2f} dist={best_row.refine_max_distance:.1f} win={best_row.W_initial:.2f} T0={best_row.T_initial_raw} (runtime={best_row.runtime_sec:.3f}s hpwl={best_row.hpwl:.2f})")
    else:
        print('\nNo candidates found for suggested default (unexpected).')


if __name__ == '__main__':
    main()
