"""
knob_analysis.py: Run systematic one-at-a-time knob analysis with specific test ranges.

This script tests each knob independently with predefined ranges:
- cooling_rate: 0.8 to 0.99 (10 experiments)
- moves_per_temp: 100 to 2000 (increments of 200, 10 experiments)
- p_refine: 0.3 to 0.8 (10 experiments, p_explore = 1.0 - p_refine)
- W_initial: 0.05 to 0.8 (10 experiments)
- batch_size: 50 to 950 (increments of 100, 10 experiments)

All other knobs are kept at their default values while testing each one.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import List, Tuple, Dict, Set, Optional
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.parsers.fabric_db import get_fabric_db
from src.parsers.pins_parser import load_and_validate
from src.parsers.netlist_parser import parse_netlist
from src.placement.placer import place_cells_greedy_sim_anneal
from src.placement.placement_utils import nets_by_cell, fixed_points_from_pins, hpwl_for_nets


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


def _pareto_flags(rows: List[Dict]) -> List[int]:
    """Mark dominated rows (1 = dominated, 0 = non-dominated)."""
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


def run_knob_analysis(design_json: str,
                      runs_per_setting: int,
                      seed: int,
                      out_prefix: Path) -> pd.DataFrame:
    """
    Run systematic knob analysis with predefined test ranges.
    
    Default values (baseline - used when not testing that knob):
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
    DEFAULT_MOVES_PER_TEMP = 1000
    DEFAULT_P_REFINE = 0.7
    DEFAULT_P_EXPLORE = 0.3
    DEFAULT_REFINE_MAX_DISTANCE = 100.0
    DEFAULT_W_INITIAL = 0.5
    DEFAULT_T_INITIAL = 'auto'
    DEFAULT_BATCH_SIZE = 200
    
    # Define test ranges (10 experiments each)
    cooling_rates = np.linspace(0.8, 0.99, 10).tolist()
    moves_per_temp_list = list(range(100, 2001, 200))  # 100, 300, 500, ..., 1900 (10 values)
    p_refine_list = np.linspace(0.3, 0.8, 10).tolist()  # 10 values from 0.3 to 0.8
    w_initial_list = np.linspace(0.05, 0.8, 10).tolist()  # 10 values from 0.05 to 0.8
    batch_size_list = list(range(50, 1001, 100))  # 50, 150, 250, ..., 950 (10 values)
    batch_size_list = list(range(50, 1001, 100))  # 50, 150, 250, ..., 950 (10 values)
    
    # Load shared inputs once
    print("[KNOB] Loading fabric, pins, and netlist...")
    fabric, fabric_df = get_fabric_db("inputs/Platform/fabric.yaml", "inputs/Platform/fabric_cells.yaml")
    pins_df, _pins_meta = load_and_validate("inputs/Platform/pins.yaml")
    logical_db, ports_df, netlist_graph = parse_netlist(design_json)
    print("[KNOB] Inputs loaded successfully.\n")

    results: List[Dict] = []
    setting_id = 0
    
    def parse_temp(t_str: str) -> Optional[float]:
        """Parse temperature string to float or None."""
        if str(t_str).lower() == 'auto':
            return None
        try:
            return float(t_str)
        except ValueError:
            return None
    
    def run_experiment(knob_name: str, knob_value,
                       cooling_rate: float, moves_per_temp: int,
                       p_refine: float, p_explore: float,
                       refine_max_distance: float, win_init: float,
                       t_init_raw: str, run_seed: int) -> Optional[Dict]:
        """Run a single placement experiment."""
        nonlocal setting_id
        setting_id += 1
        
        if p_refine + p_explore <= 0:
            return None
        
        print(f"[KNOB] Setting {setting_id} ({knob_name}={knob_value}): "
              f"alpha={cooling_rate:.3f} moves={moves_per_temp} p_ref={p_refine:.2f} "
              f"p_exp={p_explore:.2f} dist={refine_max_distance:.1f} "
              f"win={win_init:.2f} T0={t_init_raw} seed={run_seed}")
        
        t_start = time.perf_counter()
        t_initial_val = parse_temp(t_init_raw)
        
        try:
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
                sa_batch_size=DEFAULT_BATCH_SIZE if knob_name != 'batch_size' else int(knob_value),
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
                'batch_size': DEFAULT_BATCH_SIZE if knob_name != 'batch_size' else int(knob_value),
                'runtime_sec': runtime,
                'hpwl': hpwl_val,
                'seed': run_seed,
            }
        except Exception as e:
            import traceback
            print(f"[KNOB] ERROR in experiment {setting_id} ({knob_name}={knob_value}): {e}")
            print(f"[KNOB] Traceback: {traceback.format_exc()}")
            return None
    
    # 1. Test cooling_rate: 0.8 to 0.99 (10 experiments)
    print("=" * 80)
    print("TEST 1: Varying cooling_rate (alpha) from 0.8 to 0.99 (10 experiments)")
    print("=" * 80)
    for cooling_rate in cooling_rates:
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'cooling_rate', round(cooling_rate, 3),
                cooling_rate, DEFAULT_MOVES_PER_TEMP,
                DEFAULT_P_REFINE, DEFAULT_P_EXPLORE,
                DEFAULT_REFINE_MAX_DISTANCE, DEFAULT_W_INITIAL,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    print(f"[KNOB] Completed {len([r for r in results if r['knob_name'] == 'cooling_rate'])} cooling_rate experiments\n")
    
    # 2. Test moves_per_temp: 100 to 2000 (increments of 200, 10 experiments)
    print("=" * 80)
    print("TEST 2: Varying moves_per_temp from 100 to 2000 (increments of 200, 10 experiments)")
    print("=" * 80)
    for moves_per_temp in moves_per_temp_list:
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
    print(f"[KNOB] Completed {len([r for r in results if r['knob_name'] == 'moves_per_temp'])} moves_per_temp experiments\n")
    
    # 3. Test p_refine: 0.3 to 0.8 (10 experiments, p_explore = 1.0 - p_refine)
    print("=" * 80)
    print("TEST 3: Varying p_refine from 0.3 to 0.8 (10 experiments, p_explore = 1.0 - p_refine)")
    print("=" * 80)
    for p_refine in p_refine_list:
        p_explore = 1.0 - p_refine
        if p_explore < 0:
            continue
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'p_refine', round(p_refine, 3),
                DEFAULT_COOLING_RATE, DEFAULT_MOVES_PER_TEMP,
                p_refine, p_explore,
                DEFAULT_REFINE_MAX_DISTANCE, DEFAULT_W_INITIAL,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    print(f"[KNOB] Completed {len([r for r in results if r['knob_name'] == 'p_refine'])} p_refine experiments\n")
    
    # 4. Test W_initial: 0.05 to 0.8 (10 experiments)
    print("=" * 80)
    print("TEST 4: Varying W_initial from 0.05 to 0.8 (10 experiments)")
    print("=" * 80)
    for win_init in w_initial_list:
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'W_initial', round(win_init, 3),
                DEFAULT_COOLING_RATE, DEFAULT_MOVES_PER_TEMP,
                DEFAULT_P_REFINE, DEFAULT_P_EXPLORE,
                DEFAULT_REFINE_MAX_DISTANCE, win_init,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    print(f"[KNOB] Completed {len([r for r in results if r['knob_name'] == 'W_initial'])} W_initial experiments\n")

    # 5. Test batch_size: 50 to 950 (increments of 100, 10 experiments)
    print("=" * 80)
    print("TEST 5: Varying batch_size from 50 to 950 (increments of 100, 10 experiments)")
    print("=" * 80)
    for bsz in batch_size_list:
        for run_idx in range(runs_per_setting):
            run_seed = seed + setting_id + run_idx
            result = run_experiment(
                'batch_size', bsz,
                DEFAULT_COOLING_RATE, DEFAULT_MOVES_PER_TEMP,
                DEFAULT_P_REFINE, DEFAULT_P_EXPLORE,
                DEFAULT_REFINE_MAX_DISTANCE, DEFAULT_W_INITIAL,
                DEFAULT_T_INITIAL, run_seed
            )
            if result:
                results.append(result)
    print(f"[KNOB] Completed {len([r for r in results if r['knob_name'] == 'batch_size'])} batch_size experiments\n")
    
    # Pareto flagging
    print("[KNOB] Computing Pareto dominance...")
    flags = _pareto_flags(results)
    for row, flag in zip(results, flags):
        row['dominated'] = flag
    
    df = pd.DataFrame(results)
    return df


def plot_individual_knob_effects(df: pd.DataFrame, out_prefix: Path) -> None:
    """Generate individual plots showing the effect of each knob."""
    knob_names = df['knob_name'].unique()
    
    for knob_name in knob_names:
        knob_df = df[df['knob_name'] == knob_name].copy()
        
        # Try to convert to numeric for proper sorting
        numeric_vals = pd.to_numeric(knob_df['knob_value'], errors='coerce')
        if numeric_vals.notna().all():
            sort_idx = numeric_vals.argsort()
            knob_df = knob_df.iloc[sort_idx]
            x_values = numeric_vals.iloc[sort_idx]
            use_line_plot = True
        else:
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
    ap = argparse.ArgumentParser(
        description='Systematic one-at-a-time knob analysis with predefined test ranges.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Test Ranges (10 experiments each):
  - cooling_rate: 0.8 to 0.99
  - moves_per_temp: 100 to 2000 (increments of 200)
  - p_refine: 0.3 to 0.8 (p_explore = 1.0 - p_refine)
  - W_initial: 0.05 to 0.8
  - batch_size: 50 to 950 (increments of 100)
  - batch_size: 200 to 2000 (7 experiments)

Default values (used when not testing that knob):
  - cooling_rate: 0.95
  - moves_per_temp: 1000
  - p_refine: 0.7
  - p_explore: 0.3
  - refine_max_distance: 100.0
  - W_initial: 0.5
  - T_initial: 'auto'
  - batch_size: 500
        """
    )
    ap.add_argument('--design-json', default='inputs/designs/6502_mapped.json', 
                   help='Design mapped JSON path')
    ap.add_argument('--runs-per-setting', type=int, default=1, 
                   help='Repeat each setting this many times (different seeds)')
    ap.add_argument('--seed', type=int, default=42, 
                   help='Base seed for reproducibility')
    ap.add_argument('--out-prefix', default='build/knob_analysis_6502', 
                   help='Prefix for output CSV and plots')
    args = ap.parse_args()

    out_prefix = Path(args.out_prefix)
    
    print("=" * 80)
    print("SA Placer Systematic Knob Analysis")
    print("=" * 80)
    print(f"Design: {args.design_json}")
    print(f"Runs per setting: {args.runs_per_setting}")
    print(f"Base seed: {args.seed}")
    print(f"Output prefix: {out_prefix}")
    print("=" * 80)
    print("\nTest Configuration:")
    print("  - cooling_rate: 0.8 to 0.99 (10 experiments)")
    print("  - moves_per_temp: 100 to 2000 (increments of 200, 10 experiments)")
    print("  - p_refine: 0.3 to 0.8 (10 experiments)")
    print("  - W_initial: 0.05 to 0.8 (10 experiments)")
    print("  - batch_size: 50 to 950 (increments of 100, 10 experiments)")
    print("  - batch_size: 50 to 950 (increments of 100, 10 experiments)")
    print("\nDefault values (used when not testing that knob):")
    print("  - cooling_rate: 0.95")
    print("  - moves_per_temp: 1000")
    print("  - p_refine: 0.7")
    print("  - p_explore: 0.3")
    print("  - refine_max_distance: 100.0")
    print("  - W_initial: 0.5")
    print("  - T_initial: 'auto'")
    print("  - batch_size: 5200")
    print("=" * 80)
    print()
    
    df = run_knob_analysis(
        args.design_json,
        args.runs_per_setting,
        args.seed,
        out_prefix,
    )

    # Save CSV
    csv_path = out_prefix.with_suffix('.results.csv')
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    print(f"\n[KNOB] Saved results CSV: {csv_path} ({len(df)} experiments)")

    # Check if we have any results before plotting
    if len(df) == 0:
        print("\n[KNOB] WARNING: No successful experiments! Cannot generate plots.")
        print("Check the error messages above to see what went wrong.")
        return
    
    # Plot individual knob effects only
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
        print(f"  {r.knob_name}={r.knob_value}: runtime={r.runtime_sec:.3f}s hpwl={r.hpwl:.2f}um")
    
    print('\n' + '=' * 80)
    print("Analysis Complete!")
    print('=' * 80)


if __name__ == '__main__':
    main()

