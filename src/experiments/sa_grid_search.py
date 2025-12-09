"""
sa_grid_search.py: Run one-at-a-time knob analysis experiments over Simulated Annealing (SA) placer knobs
for a single design (default: 6502) and produce runtime vs HPWL plots showing the effect of each knob.

This script uses a systematic approach: it varies ONE knob at a time while keeping all others constant
at their default values. This allows clear identification of each knob's individual effect.

Default knob values (used as baseline):
    cooling_rate: 0.95
    moves_per_temp: 200
    p_refine: 0.7
    p_explore: 0.3
    refine_max_distance: 100.0
    W_initial: 0.5
    T_initial: 'auto'

Usage example (PowerShell):
        python -m src.experiments.sa_grid_search `
                --design-json inputs/designs/6502_mapped.json `
                --cooling-list 0.80 0.85 0.90 0.95 0.99 `
                --moves-list 50 100 150 200 300 `
                --refine-prob-list 0.5 0.6 0.7 0.8 `
                --explore-prob-list 0.2 0.3 0.4 0.5 `
                --refine-dist-list 50 100 150 200 `
                --win-init-list 0.3 0.5 0.7 `
                --temp-initial-list auto 10 50 100 `
                --runs-per-setting 1 `
                --out-prefix build/sa_6502_knob_analysis

This will generate:
    - CSV: build/sa_6502_knob_analysis.results.csv
    - Combined scatter plot: build/sa_6502_knob_analysis.scatter.png
    - Individual knob effect plots: build/sa_6502_knob_analysis.*_effect.png

Columns in CSV:
    design, knob_name, knob_value, cooling_rate, moves_per_temp, p_refine, p_explore,
    refine_max_distance, W_initial, T_initial_raw, runtime_sec, hpwl, seed, dominated (0/1)

The knob_name column indicates which knob was varied for that experiment.
"""
from __future__ import annotations

import argparse
import time
import random
from pathlib import Path
from typing import List, Tuple, Dict, Set, Optional
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


def run_one_at_a_time_knob_analysis(design_json: str,
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
    """
    Run knob analysis by varying ONE knob at a time while keeping others constant.
    
    Default values (baseline):
        cooling_rate: 0.95
        moves_per_temp: 200
        p_refine: 0.7
        p_explore: 0.3
        refine_max_distance: 100.0
        W_initial: 0.5
        T_initial: 'auto' (None)
    """
    # Define default values for all knobs
    DEFAULT_COOLING_RATE = 0.95
    DEFAULT_MOVES_PER_TEMP = 200
    DEFAULT_P_REFINE = 0.7
    DEFAULT_P_EXPLORE = 0.3
    DEFAULT_REFINE_MAX_DISTANCE = 100.0
    DEFAULT_W_INITIAL = 0.5
    DEFAULT_T_INITIAL = 'auto'
    
    # Load shared inputs once
    fabric, fabric_df = get_fabric_db("inputs/Platform/fabric.yaml", "inputs/Platform/fabric_cells.yaml")
    pins_df, _pins_meta = load_and_validate("inputs/Platform/pins.yaml")
    logical_db, ports_df, netlist_graph = parse_netlist(design_json)

    results: List[Dict] = []
    setting_id = 0
    
    # Helper function to interpret temperature
    def parse_temp(t_str: str) -> Optional[float]:
        if str(t_str).lower() == 'auto':
            return None
        try:
            return float(t_str)
        except ValueError:
            return None
    
    # Helper function to run a single experiment
    def run_experiment(knob_name: str, knob_value, 
                       cooling_rate: float, moves_per_temp: int,
                       p_refine: float, p_explore: float,
                       refine_max_distance: float, win_init: float,
                       t_init_raw: str, run_seed: int) -> Dict:
        nonlocal setting_id
        setting_id += 1
        
        # Validate probability combo
        if p_refine + p_explore <= 0:
            return None
        
        print(f"[KNOB] Setting {setting_id} ({knob_name}={knob_value}): "
              f"alpha={cooling_rate} moves={moves_per_temp} p_ref={p_refine:.2f} "
              f"p_exp={p_explore:.2f} dist={refine_max_distance:.1f} "
              f"win={win_init:.2f} T0={t_init_raw} seed={run_seed}")
        
        t_start = time.perf_counter()
        t_initial_val = parse_temp(t_init_raw)
        
        # Run placer
        updated_pins, placement_df, _val, _sa_hpwl = place_cells_greedy_sim_anneal(
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
            sa_refine_max_distance=refine_max_distance,
            sa_W_initial=win_init,
            sa_seed=run_seed,
        )
        t_end = time.perf_counter()
        hpwl_val = _compute_global_hpwl(placement_df, updated_pins, netlist_graph)
        runtime = t_end - t_start
        
        return {
            'design': Path(design_json).stem.replace('_mapped', ''),
            'knob_name': knob_name,
            'knob_value': knob_value,
            'cooling_rate': cooling_rate,
            'moves_per_temp': moves_per_temp,
            'p_refine': p_refine,
            'p_explore': p_explore,
            'refine_max_distance': refine_max_distance,
            'W_initial': win_init,
            'T_initial_raw': t_init_raw,
            'runtime_sec': runtime,
            'hpwl': hpwl_val,
            'seed': run_seed,
        }
    
    # 1. Vary cooling_rate only
    print("\n=== Varying cooling_rate (alpha) ===")
    for cooling_rate in cooling_list:
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'cooling_rate', cooling_rate,
                cooling_rate, DEFAULT_MOVES_PER_TEMP,
                DEFAULT_P_REFINE, DEFAULT_P_EXPLORE,
                DEFAULT_REFINE_MAX_DISTANCE, DEFAULT_W_INITIAL,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    
    # 2. Vary moves_per_temp only
    print("\n=== Varying moves_per_temp (N) ===")
    for moves_per_temp in moves_list:
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'moves_per_temp', moves_per_temp,
                DEFAULT_COOLING_RATE, moves_per_temp,
                DEFAULT_P_REFINE, DEFAULT_P_EXPLORE,
                DEFAULT_REFINE_MAX_DISTANCE, DEFAULT_W_INITIAL,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    
    # 3. Vary p_refine only (adjust p_explore to maintain sum = 1.0)
    print("\n=== Varying p_refine (with p_explore = 1.0 - p_refine) ===")
    for p_refine in refine_prob_list:
        p_explore = 1.0 - p_refine
        if p_explore < 0:
            continue
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'p_refine', p_refine,
                DEFAULT_COOLING_RATE, DEFAULT_MOVES_PER_TEMP,
                p_refine, p_explore,
                DEFAULT_REFINE_MAX_DISTANCE, DEFAULT_W_INITIAL,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    
    # 4. Vary p_explore only (adjust p_refine to maintain sum = 1.0)
    print("\n=== Varying p_explore (with p_refine = 1.0 - p_explore) ===")
    for p_explore in explore_prob_list:
        p_refine = 1.0 - p_explore
        if p_refine < 0:
            continue
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'p_explore', p_explore,
                DEFAULT_COOLING_RATE, DEFAULT_MOVES_PER_TEMP,
                p_refine, p_explore,
                DEFAULT_REFINE_MAX_DISTANCE, DEFAULT_W_INITIAL,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    
    # 5. Vary refine_max_distance only
    print("\n=== Varying refine_max_distance ===")
    for refine_dist in refine_dist_list:
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'refine_max_distance', refine_dist,
                DEFAULT_COOLING_RATE, DEFAULT_MOVES_PER_TEMP,
                DEFAULT_P_REFINE, DEFAULT_P_EXPLORE,
                refine_dist, DEFAULT_W_INITIAL,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    
    # 6. Vary W_initial only
    print("\n=== Varying W_initial (exploration window) ===")
    for win_init in win_init_list:
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'W_initial', win_init,
                DEFAULT_COOLING_RATE, DEFAULT_MOVES_PER_TEMP,
                DEFAULT_P_REFINE, DEFAULT_P_EXPLORE,
                DEFAULT_REFINE_MAX_DISTANCE, win_init,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    
    # 7. Vary T_initial only
    print("\n=== Varying T_initial (initial temperature) ===")
    for t_init_raw in temp_initial_list:
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'T_initial', t_init_raw,
                DEFAULT_COOLING_RATE, DEFAULT_MOVES_PER_TEMP,
                DEFAULT_P_REFINE, DEFAULT_P_EXPLORE,
                DEFAULT_REFINE_MAX_DISTANCE, DEFAULT_W_INITIAL,
                t_init_raw, run_seed
            )
            if result:
                results.append(result)
    
    # Pareto flagging
    flags = _pareto_flags(results)
    for row, flag in zip(results, flags):
        row['dominated'] = flag
    
    df = pd.DataFrame(results)
    return df


def plot_runtime_hpwl(df: pd.DataFrame, out_prefix: Path) -> Path:
    """Plot combined scatter plot with color-coding by knob name."""
    plt.figure(figsize=(12, 8))
    
    # Color map for different knobs
    knob_colors = {
        'cooling_rate': 'tab:blue',
        'moves_per_temp': 'tab:orange',
        'p_refine': 'tab:green',
        'p_explore': 'tab:red',
        'refine_max_distance': 'tab:purple',
        'W_initial': 'tab:brown',
        'T_initial': 'tab:pink',
    }
    
    # Plot points grouped by knob
    for knob_name in df['knob_name'].unique():
        knob_df = df[df['knob_name'] == knob_name]
        color = knob_colors.get(knob_name, 'gray')
        plt.scatter(knob_df['runtime_sec'], knob_df['hpwl'],
                   c=color, label=knob_name, s=60, alpha=0.7, edgecolors='black', linewidth=0.5, zorder=2)
    
    # Highlight Pareto front
    dominated = df['dominated'] == 1
    non_dom = df['dominated'] == 0
    pareto_points = df.loc[non_dom].sort_values('runtime_sec')
    if len(pareto_points) > 0:
        plt.plot(pareto_points['runtime_sec'], pareto_points['hpwl'],
                c='black', linestyle='--', alpha=0.5, linewidth=2, label='Pareto Front', zorder=1)

    plt.xlabel('Runtime (s)', fontsize=12)
    plt.ylabel('Final HPWL (um)', fontsize=12)
    design_name = df['design'].iloc[0] if 'design' in df.columns else "Unknown"
    plt.title(f"SA Placer Knob Analysis: Runtime vs Quality ({design_name})\n(One knob varied at a time)", fontsize=13)
    
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.gca().spines['top'].set_visible(False)
    plt.gca().spines['right'].set_visible(False)
    
    plt.legend(frameon=True, fancybox=True, framealpha=0.9, loc='best')
    
    out_path = out_prefix.with_suffix('.scatter.png')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    print(f"[KNOB] Saved combined scatter plot: {out_path}")
    return out_path


def plot_individual_knob_effects(df: pd.DataFrame, out_prefix: Path) -> None:
    """Generate individual plots showing the effect of each knob."""
    knob_names = df['knob_name'].unique()
    
    for knob_name in knob_names:
        knob_df = df[df['knob_name'] == knob_name].copy()
        
        # Try to convert to numeric for proper sorting
        numeric_vals = pd.to_numeric(knob_df['knob_value'], errors='coerce')
        if numeric_vals.notna().all():
            # All numeric - sort numerically
            sort_idx = numeric_vals.argsort()
            knob_df = knob_df.iloc[sort_idx]
            x_values = numeric_vals.iloc[sort_idx]
            use_line_plot = True
        else:
            # Mixed or string types - sort by string representation
            knob_df = knob_df.sort_values('knob_value', key=lambda x: x.astype(str))
            x_values = knob_df['knob_value']
            use_line_plot = False
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
        
        # Left plot: knob_value vs HPWL
        if use_line_plot:
            ax1.plot(x_values, knob_df['hpwl'], 'o-', linewidth=2, markersize=8, color='tab:blue')
        else:
            ax1.scatter(range(len(x_values)), knob_df['hpwl'], s=80, color='tab:blue')
            ax1.set_xticks(range(len(x_values)))
            ax1.set_xticklabels([str(v) for v in x_values], rotation=45, ha='right')
        
        ax1.set_xlabel(f'{knob_name}', fontsize=11)
        ax1.set_ylabel('Final HPWL (um)', fontsize=11)
        ax1.set_title(f'Effect of {knob_name} on HPWL', fontsize=12)
        ax1.grid(True, linestyle=':', alpha=0.6)
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_visible(False)
        
        # Right plot: knob_value vs Runtime
        if use_line_plot:
            ax2.plot(x_values, knob_df['runtime_sec'], 'o-', linewidth=2, markersize=8, color='tab:orange')
        else:
            ax2.scatter(range(len(x_values)), knob_df['runtime_sec'], s=80, color='tab:orange')
            ax2.set_xticks(range(len(x_values)))
            ax2.set_xticklabels([str(v) for v in x_values], rotation=45, ha='right')
        
        ax2.set_xlabel(f'{knob_name}', fontsize=11)
        ax2.set_ylabel('Runtime (s)', fontsize=11)
        ax2.set_title(f'Effect of {knob_name} on Runtime', fontsize=12)
        ax2.grid(True, linestyle=':', alpha=0.6)
        ax2.spines['top'].set_visible(False)
        ax2.spines['right'].set_visible(False)
        
        plt.tight_layout()
        out_path = out_prefix.parent / f"{out_prefix.name}_{knob_name}_effect.png"
        plt.savefig(out_path, dpi=150)
        plt.close()
        print(f"[KNOB] Saved individual plot: {out_path}")


def main():
    ap = argparse.ArgumentParser(description='One-at-a-time knob analysis for SA placer knobs. Varies one knob at a time while keeping others constant.')
    ap.add_argument('--design-json', default='inputs/designs/6502_mapped.json', help='Design mapped JSON path')
    ap.add_argument('--cooling-list', type=float, nargs='+', default=[0.80,0.85,0.90,0.95,0.99], help='Cooling rates (alpha values) to test')
    ap.add_argument('--moves-list', type=int, nargs='+', default=[50,100,150,200,300], help='Moves per temperature (sa_moves_per_temp) values to test')
    ap.add_argument('--refine-prob-list', type=float, nargs='+', default=[0.5,0.6,0.7,0.8], help='Refine move probabilities (p_explore will be 1.0 - p_refine)')
    ap.add_argument('--explore-prob-list', type=float, nargs='+', default=[0.2,0.3,0.4,0.5], help='Explore move probabilities (p_refine will be 1.0 - p_explore)')
    ap.add_argument('--refine-dist-list', type=float, nargs='+', default=[50.0,100.0,150.0,200.0], help='Refine max Manhattan distance list (microns)')
    ap.add_argument('--win-init-list', type=float, nargs='+', default=[0.3,0.5,0.7], help='Initial explore window fraction list')
    ap.add_argument('--temp-initial-list', nargs='+', default=['auto', '10', '50', '100'], help="Initial temperature values (float) or 'auto'")
    ap.add_argument('--runs-per-setting', type=int, default=1, help='Repeat each setting this many times (different seeds)')
    ap.add_argument('--seed', type=int, default=42, help='Base seed for reproducibility')
    ap.add_argument('--out-prefix', default='build/sa_6502_knob_analysis', help='Prefix for output CSV and plots')
    args = ap.parse_args()

    out_prefix = Path(args.out_prefix)
    
    print("=" * 80)
    print("SA Placer Knob Analysis: One-at-a-Time Approach")
    print("=" * 80)
    print(f"Design: {args.design_json}")
    print(f"Default values (baseline):")
    print(f"  cooling_rate: 0.95")
    print(f"  moves_per_temp: 200")
    print(f"  p_refine: 0.7")
    print(f"  p_explore: 0.3")
    print(f"  refine_max_distance: 100.0")
    print(f"  W_initial: 0.5")
    print(f"  T_initial: 'auto'")
    print("=" * 80)
    
    df = run_one_at_a_time_knob_analysis(
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
    print(f"\n[KNOB] Saved results CSV: {csv_path}")

    # Plot combined scatter
    plot_runtime_hpwl(df, out_prefix)
    
    # Plot individual knob effects
    plot_individual_knob_effects(df, out_prefix)

    # Print summary by knob
    print('\n' + '=' * 80)
    print("Summary by Knob:")
    print('=' * 80)
    for knob_name in sorted(df['knob_name'].unique()):
        knob_df = df[df['knob_name'] == knob_name]
        best_hpwl = knob_df.loc[knob_df['hpwl'].idxmin()]
        fastest = knob_df.loc[knob_df['runtime_sec'].idxmin()]
        print(f"\n{knob_name}:")
        print(f"  Best HPWL: {best_hpwl['knob_value']} -> HPWL={best_hpwl['hpwl']:.2f}um, runtime={best_hpwl['runtime_sec']:.3f}s")
        print(f"  Fastest: {fastest['knob_value']} -> HPWL={fastest['hpwl']:.2f}um, runtime={fastest['runtime_sec']:.3f}s")
    
    # Print Pareto summary
    pareto_df = df[df['dominated'] == 0].sort_values(by=['runtime_sec'])
    print('\n' + '=' * 80)
    print("Pareto Front (non-dominated points):")
    print('=' * 80)
    for _, r in pareto_df.iterrows():
        print(f"  {r.knob_name}={r.knob_value}: alpha={r.cooling_rate:.2f} moves={int(r.moves_per_temp)} "
              f"p_ref={r.p_refine:.2f} p_exp={r.p_explore:.2f} dist={r.refine_max_distance:.1f} "
              f"win={r.W_initial:.2f} T0={r.T_initial_raw} runtime={r.runtime_sec:.3f}s hpwl={r.hpwl:.2f}")

    # Recommend best settings per knob
    print('\n' + '=' * 80)
    print("Recommended Settings (best HPWL per knob):")
    print('=' * 80)
    for knob_name in sorted(df['knob_name'].unique()):
        knob_df = df[df['knob_name'] == knob_name]
        best = knob_df.loc[knob_df['hpwl'].idxmin()]
        print(f"  {knob_name}: {best['knob_value']} (HPWL={best['hpwl']:.2f}um, runtime={best['runtime_sec']:.3f}s)")


if __name__ == '__main__':
    main()
