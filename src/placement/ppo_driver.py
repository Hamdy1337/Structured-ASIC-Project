import argparse
import os
import sys
import datetime
import time
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import numpy as np

# Add src to path if needed (though usually -m src... handles it)
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from src.parsers.fabric_db import get_fabric_db
from src.parsers.pins_parser import load_and_validate
from src.parsers.netlist_parser import parse_netlist, get_netlist_graph
from src.parsers.fabric_cells_parser import parse_fabric_cells_file
from src.placement.placer_rl import (
    run_greedy_sa_then_rl_pipeline,
    hpwl_of_nets,
    fixed_points_from_pins,
    nets_map_from_graph_df,
)
from src.placement.placement_mapper import map_placement_to_physical_cells, generate_map_file
from src.cts.htree_builder import run_eco_flow
from src.Visualization.cts_plotter import plot_cts_tree_interactive
from src.Visualization.heatmap import plot_placement_heatmap
from src.placement.placer import generate_net_hpwl_histogram

class Tee:
    def __init__(self, *files):
        self.files = files

    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()

def _pos_map_from_df(df: pd.DataFrame) -> Dict[str, Tuple[float, float]]:
    return {str(r.cell_name): (float(r.x_um), float(r.y_um)) for r in df.itertuples(index=False)}

def main():
    ap = argparse.ArgumentParser(description="Run Greedy+SA then PPO swap refiner and report ΔHPWL.")
    ap.add_argument("--design-json", default="inputs/designs/6502_mapped.json", help="Path to [design]_mapped.json")
    ap.add_argument("--fabric-yaml", default="inputs/Platform/fabric.yaml", help="Path to fabric.yaml")
    ap.add_argument("--pins-yaml", default="inputs/Platform/pins.yaml", help="Path to pins.yaml")
    ap.add_argument("--fabric-cells-yaml", default="inputs/Platform/fabric_cells.yaml", help="Path to fabric_cells.yaml")
    ap.add_argument("--max-action-full", type=int, default=1024)
    ap.add_argument("--full-placer-eps", type=int, default=0, help="PPO episodes for full placer (0 = skip training)")
    ap.add_argument("--swap-train-eps", type=int, default=60, help="PPO episodes for swap refiner per-batch")
    ap.add_argument("--swap-steps-per-ep", type=int, default=50, help="Environment steps per swap episode")
    ap.add_argument("--batch-size", type=int, default=300)
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda", "mps"])
    ap.add_argument("--max-train-batches", type=int, default=50, help="Max training batches for swap PPO")
    ap.add_argument("--max-apply-batches", type=int, default=None, help="Max batches to apply refinement; default all")
    ap.add_argument("--full-steps-per-ep", type=int, default=512, help="Steps per episode for full placer")
    ap.add_argument("--out-csv", default="build/6502.csv")
    ap.add_argument("--timing", action="store_true", help="Enable detailed RL timing logs")
    ap.add_argument("--full-log-csv", default=None, help="Optional CSV path to log per-episode full placer PPO metrics")
    ap.add_argument("--swap-log-csv", default=None, help="Optional CSV path to log per-episode swap refiner PPO metrics")
    ap.add_argument("--ppo-clip", type=float, default=0.2, help="PPO clip epsilon")
    ap.add_argument("--ppo-value-coef", type=float, default=1.0, help="Value loss coefficient")
    ap.add_argument("--ppo-entropy-coef", type=float, default=0.01, help="Entropy bonus coefficient")
    ap.add_argument("--ppo-max-grad-norm", type=float, default=0.5, help="Max gradient norm for clipping")
    ap.add_argument("--sa-iters", type=int, default=5000, help="SA moves per temp")
    args = ap.parse_args()

    # Determine output directory and prefix
    design_name = Path(args.design_json).stem.replace("_mapped", "")
    build_dir = Path("build") / design_name
    build_dir.mkdir(parents=True, exist_ok=True)

    # Use out-csv stem as the prefix for all artifacts if provided
    # e.g. if out-csv is "build/6502_rl_4hr.csv", prefix is "6502_rl_4hr"
    # otherwise defaults to design_name
    output_prefix = Path(args.out_csv).stem if args.out_csv else design_name
    
    # Setup Logging
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = log_dir / f"{output_prefix}_flow_{timestamp}.log"
    
    print(f"Logging to: {log_file_path}")
    
    f_log = open(log_file_path, 'w', encoding='utf-8')
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    sys.stdout = Tee(original_stdout, f_log)
    sys.stderr = Tee(original_stderr, f_log)

    try:
        print(f"=== Starting RL Flow for {design_name} (Prefix: {output_prefix}) ===")
        print(f"Time: {datetime.datetime.now()}")

        # Load inputs
        fabric, fabric_df = get_fabric_db(args.fabric_yaml, args.fabric_cells_yaml)
        pins_df, _pins_meta = load_and_validate(args.pins_yaml)
        logical_db, ports_df, netlist_graph = parse_netlist(args.design_json)
        
        # Also need detailed fabric cells for mapping
        _fabric_cells, fabric_cells_df = parse_fabric_cells_file(args.fabric_cells_yaml)

        # Run pipeline
        t_pipeline_start = time.perf_counter()
        print("\n[Step 1] Running Greedy+SA Baseline and RL Optimization...")
        updated_pins, placement_df, refined_df, baseline_sa_hpwl = run_greedy_sa_then_rl_pipeline(
            fabric,
            fabric_df,
            pins_df,
            ports_df,
            netlist_graph,
            max_action_full=args.max_action_full,
            full_placer_train_eps=args.full_placer_eps,
            swap_refine_train_eps=args.swap_train_eps,
            batch_size=args.batch_size,
            device=args.device,
            max_train_batches=args.max_train_batches,
            max_apply_batches=args.max_apply_batches,
            full_steps_per_ep=args.full_steps_per_ep,
            swap_steps_per_ep=args.swap_steps_per_ep,
            enable_timing=args.timing,
            full_log_csv=args.full_log_csv,
            swap_log_csv=args.swap_log_csv,
            ppo_clip_eps=args.ppo_clip,
            ppo_value_coef=args.ppo_value_coef,
            ppo_entropy_coef=args.ppo_entropy_coef,
            ppo_max_grad_norm=args.ppo_max_grad_norm,
            sa_moves_per_temp=args.sa_iters,
        )
        t_pipeline_end = time.perf_counter()

        # Save Baseline Placement
        placement_out_path = build_dir / f"{design_name}_greedy_sa_placement.csv"
        placement_df.to_csv(placement_out_path, index=False)
        print(f"Saved Greedy+SA placement to: {placement_out_path}")

        # Compute Metrics
        nets = nets_map_from_graph_df(netlist_graph)
        fixed_pts = fixed_points_from_pins(updated_pins)

        t_hpwl_start = time.perf_counter()
        hpwl_before = baseline_sa_hpwl
        hpwl_after = hpwl_of_nets(nets, _pos_map_from_df(refined_df), fixed_pts)
        t_hpwl_end = time.perf_counter()

        delta = hpwl_after - hpwl_before
        pct = (delta / hpwl_before * 100.0) if hpwl_before > 0 else 0.0

        # Save Refined Placement (use custom prefix)
        out_path = build_dir / f"{output_prefix}.csv"
        refined_df.to_csv(out_path, index=False)
        print(f"Saved refined placement to: {out_path}")
        
        print("\n==== Metrics Comparison ====")
        print(f"Greedy+SA HPWL:   {hpwl_before:.3f} um")
        print(f"PPO Refined HPWL: {hpwl_after:.3f} um")
        print(f"Delta:            {delta:.3f} ({pct:.2f}%)")
        print(f"Total Time:       {t_pipeline_end - t_pipeline_start:.3f}s")
        
        # --- Visualization and CTS Integration ---
        
        print("\n[Step 2] Generating Visualizations...")
        
        # 1. Heatmaps
        print("Generating heatmaps...")
        plot_placement_heatmap(
            placement_out_path, 
            output_path=build_dir / f"{design_name}_greedy_sa_placement_heatmap.png",
            title=f"Greedy+SA Placement Density - {design_name}"
        )
        plot_placement_heatmap(
            out_path, 
            output_path=build_dir / f"{output_prefix}_heatmap.png",
            title=f"RL Refined Placement Density - {output_prefix}"
        )
        
        # 2. Map to Physical Cells
        print("\n[Step 3] Mapping to Physical Cells and generating .map file...")
        physical_placement_df = map_placement_to_physical_cells(
            refined_df, 
            fabric_cells_df, 
            fabric_df
        )
        map_file_path = build_dir / f"{output_prefix}.map"
        generate_map_file(physical_placement_df, map_file_path, design_name)
        
        # 3. Running CTS & ECO Flow
        print("\n[Step 4] Running CTS & ECO Flow...")
        try:
            run_eco_flow(
                design_name=design_name,
                netlist_path=args.design_json,
                map_file_path=str(map_file_path),
                fabric_cells_path=args.fabric_cells_yaml,
                fabric_path=args.fabric_yaml,
                output_dir=str(build_dir),
                pins_path=args.pins_yaml
            )
            # Note: run_eco_flow generates files based on map_file_path but might use internal naming
            # For safety, let's assume it might still generate generic names, but at least the input map is unique.
        except Exception as e:
            print(f"⚠️  CTS/ECO Flow failed: {e}")
        
        # 4. CTS Visualization
        print("\n[Step 5] Creating CTS Visualization...")
        # CTS flow typically outputs [design]_cts.json. If we want unique, we might need to rename it
        # But for now, let's look for the standard CTS output
        cts_json_path = str(build_dir / f"{design_name}_cts.json")
        cts_html_path = str(build_dir / f"{output_prefix}_cts.html")
        
        if os.path.exists(cts_json_path):
            try:
                plot_cts_tree_interactive(
                    placement_csv=str(out_path),
                    fabric_cells_yaml=args.fabric_cells_yaml,
                    cts_json=cts_json_path,
                    output_path=cts_html_path,
                    design_name=design_name
                )
                print(f"CTS Visualization saved to: {cts_html_path}")
            except Exception as e:
                print(f"⚠️  CTS Visualization failed: {e}")
        else:
            print(f"⚠️  CTS JSON not found, skipping visualization.")
        
        # 5. Wirelength Histogram
        print("\n[Step 6] Generating Wirelength Histogram...")
        try:
            generate_net_hpwl_histogram(
                placement_df=refined_df,
                updated_pins=updated_pins,
                netlist_graph=netlist_graph,
                design_name=output_prefix,
                build_dir=build_dir
            )
        except Exception as e:
            print(f"⚠️  Histogram generation failed: {e}")
        
        print(f"\n=== RL Flow Complete for {design_name} ===")
        print(f"Outputs in: {build_dir}")

    except Exception as e:
        print(f"\n❌ ERROR: RL Flow failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        f_log.close()
        print(f"Log saved to: {log_file_path}")

if __name__ == "__main__":
    main()
