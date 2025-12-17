"""
visualize_knob_csv.py

Reads the CSV output from `knob_sweep_parallel.py` and generates:
1. Pareto Frontier Plot (HPWL vs Runtime)
2. Individual Knob Plots (Knob Value vs HPWL/Runtime)
3. Text Summary of best parameters

Usage:
    python3 src/experiments/visualize_knob_csv.py --csv build/knob_parallel_6502.csv --out-dir build/plots
"""

import argparse
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
import sys

def plot_pareto(df: pd.DataFrame, out_path: Path):
    """Plot Runtime vs HPWL with Pareto frontier."""
    plt.figure(figsize=(10, 7))
    
    # Ensure dominated flag exists
    if 'dominated' not in df.columns:
        # Simple naive calculation if missing
        flags = []
        rows = df.to_dict('records')
        for i, a in enumerate(rows):
            is_dominated = 0
            for j, b in enumerate(rows):
                if i == j: continue
                # Minimizing both HPWL and Runtime
                if (b['runtime_sec'] <= a['runtime_sec'] and b['hpwl'] <= a['hpwl'] and
                        (b['runtime_sec'] < a['runtime_sec'] or b['hpwl'] < a['hpwl'])):
                    is_dominated = 1
                    break
            flags.append(is_dominated)
        df['dominated'] = flags

    dominated = df[df['dominated'] == 1]
    pareto = df[df['dominated'] == 0].sort_values('runtime_sec')

    # Scatter plots
    plt.scatter(dominated['runtime_sec'], dominated['hpwl'], 
                c='lightgray', alpha=0.6, label='Dominated', zorder=1)
    plt.scatter(pareto['runtime_sec'], pareto['hpwl'], 
                c='tab:blue', edgecolors='k', s=80, label='Pareto Front', zorder=2)
    
    # Connect Pareto points
    plt.plot(pareto['runtime_sec'], pareto['hpwl'], 
             c='tab:blue', linestyle='--', alpha=0.5, zorder=1)

    # Annotations for Pareto points (limited to avoid clutter)
    for i, r in pareto.iterrows():
        # Label with the knob that was varying, if identifiable
        label = f"{r['knob_name']}={r['knob_value']}"
        plt.annotate(label, (r['runtime_sec'], r['hpwl']),
                     textcoords="offset points", xytext=(5, 5), fontsize=8,
                     bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7))

    plt.xlabel('Runtime (s)')
    plt.ylabel('HPWL (um)')
    plt.title('Trade-off: Runtime vs Quality (Pareto Frontier)')
    plt.legend()
    plt.grid(True, linestyle=':', alpha=0.6)
    
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"[PLOT] Saved Pareto plot: {out_path}")

def plot_knob_effect(df: pd.DataFrame, knob_name: str, out_path: Path):
    """Plot effect of a single knob on HPWL and Runtime."""
    sub = df[df['knob_name'] == knob_name].copy()
    if sub.empty:
        return

    # sort by knob value
    # Try numeric sort
    try:
        sub['knob_value_num'] = pd.to_numeric(sub['knob_value'])
        sub = sub.sort_values('knob_value_num')
        x_vals = sub['knob_value_num']
        is_numeric = True
    except ValueError:
        sub = sub.sort_values('knob_value')
        x_vals = sub['knob_value'].astype(str)
        is_numeric = False

    fig, ax1 = plt.subplots(figsize=(10, 6))

    color = 'tab:blue'
    ax1.set_xlabel(knob_name)
    ax1.set_ylabel('HPWL (um)', color=color)
    
    if is_numeric:
        ax1.plot(x_vals, sub['hpwl'], color=color, marker='o', label='HPWL')
    else:
        ax1.scatter(x_vals, sub['hpwl'], color=color, label='HPWL')
        
    ax1.tick_params(axis='y', labelcolor=color)
    ax1.grid(True, linestyle=':', alpha=0.6)

    # Twin axis for Runtime
    ax2 = ax1.twinx() 
    color = 'tab:orange'
    ax2.set_ylabel('Runtime (s)', color=color)
    
    if is_numeric:
        ax2.plot(x_vals, sub['runtime_sec'], color=color, marker='x', linestyle='--', label='Runtime')
    else:
        ax2.scatter(x_vals, sub['runtime_sec'], color=color, marker='x', label='Runtime')
        
    ax2.tick_params(axis='y', labelcolor=color)

    plt.title(f'Effect of {knob_name}')
    fig.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"[PLOT] Saved knob plot: {out_path}")

def main():
    parser = argparse.ArgumentParser(description="Visualize knob sweep results")
    parser.add_argument("--csv", required=True, help="Input CSV file")
    parser.add_argument("--out-dir", default="build/plots", help="Output directory for plots")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Error: CSV file not found: {csv_path}")
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading data from {csv_path}...")
    df = pd.read_csv(csv_path)
    
    # 1. Pareto Plot
    plot_pareto(df, out_dir / "pareto_frontier.png")

    # 2. Individual Knob Plots
    knob_names = df['knob_name'].unique()
    for knob in knob_names:
        plot_knob_effect(df, knob, out_dir / f"effect_{knob}.png")

    # 3. Best Configuration Summary
    print("\n" + "="*60)
    print("BEST CONFIGURATIONS SUMMARY")
    print("="*60)
    
    # Overall best HPWL
    best_hpwl_idx = df['hpwl'].idxmin()
    best_hpwl = df.loc[best_hpwl_idx]
    print(f"Lowest HPWL found: {best_hpwl['hpwl']:.2f} um")
    print(f"  Knob:    {best_hpwl['knob_name']} = {best_hpwl['knob_value']}")
    print(f"  Runtime: {best_hpwl['runtime_sec']:.3f} s")
    print("-" * 60)

    # Pareto Front Summary
    if 'dominated' in df.columns:
        pareto = df[df['dominated'] == 0].sort_values('hpwl')
        print(f"Pareto Front ({len(pareto)} points):")
        for _, r in pareto.iterrows():
            print(f"  Running {r['knob_name']}={r['knob_value']}: HPWL={r['hpwl']:.2f}, Time={r['runtime_sec']:.3f}s")
    
    print("="*60)
    print(f"Plots saved to: {out_dir}")

if __name__ == "__main__":
    main()
