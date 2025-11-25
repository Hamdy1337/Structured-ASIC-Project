"""
plot_sa_results.py: Read an existing SA grid search CSV and regenerate the Pareto scatter plot.

Usage:
    python -m src.experiments.plot_sa_results --csv build/sa_6502_ext.results.csv --out build/sa_6502_ext_replot.png
"""

import argparse
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

def plot_runtime_hpwl(df: pd.DataFrame, out_path: Path):
    plt.figure(figsize=(10, 7))
    
    # Ensure 'dominated' column exists, if not recompute or assume all 0
    if 'dominated' not in df.columns:
        # Simple re-computation of dominance for plotting purposes
        flags = []
        rows = df.to_dict('records')
        for i, a in enumerate(rows):
            dominated = 0
            for j, b in enumerate(rows):
                if i == j: continue
                if (b['runtime_sec'] <= a['runtime_sec'] and b['hpwl'] <= a['hpwl'] and
                        (b['runtime_sec'] < a['runtime_sec'] or b['hpwl'] < a['hpwl'])):
                    dominated = 1
                    break
            flags.append(dominated)
        df['dominated'] = flags

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

    # Annotate Pareto points
    for _, r in pareto_points.iterrows():
        # Construct label from available columns
        label_parts = []
        if 'cooling_rate' in r: label_parts.append(f"α={r.cooling_rate:.2f}")
        if 'moves_per_temp' in r: label_parts.append(f"N={int(r.moves_per_temp)}")
        
        label = "\n".join(label_parts) if label_parts else "Pareto"
        
        plt.annotate(label,
                     (r.runtime_sec, r.hpwl),
                     textcoords='offset points', 
                     xytext=(5, 5), 
                     fontsize=8,
                     bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.8),
                     zorder=3)

    plt.xlabel('Runtime (s)', fontsize=10)
    plt.ylabel('Final HPWL (um)', fontsize=10)
    design_name = df['design'].iloc[0] if 'design' in df.columns else "Unknown Design"
    plt.title(f"SA Placer Trade-off: Runtime vs Quality ({design_name})", fontsize=12)
    
    # Clean up grid and spines
    plt.grid(True, linestyle=':', alpha=0.6)
    plt.gca().spines['top'].set_visible(False)
    plt.gca().spines['right'].set_visible(False)
    
    plt.legend(frameon=True, fancybox=True, framealpha=0.9)
    
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    print(f"[PLOT] Saved scatter plot: {out_path}")

def main():
    ap = argparse.ArgumentParser(description="Regenerate SA scatter plot from CSV.")
    ap.add_argument("--csv", required=True, help="Path to results CSV file")
    ap.add_argument("--out", required=True, help="Path to output PNG file")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Error: CSV file not found at {csv_path}")
        return

    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} rows from {csv_path}")
    
    plot_runtime_hpwl(df, Path(args.out))

if __name__ == "__main__":
    main()
