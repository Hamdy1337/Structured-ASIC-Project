import argparse
import os
import time
from pathlib import Path
from typing import Dict, Tuple
"""Run Greedy+SA placement followed by PPO swap refiner and report HPWL delta.
    Commands:
        python -m src.placement.ppo_driver \
            --design-json inputs/designs/6502_mapped.json \
            --fabric-yaml inputs/Platform/fabric.yaml \
            --pins-yaml inputs/Platform/pins.yaml \
            --fabric-cells-yaml inputs/Platform/fabric_cells.yaml \
            --out-csv build/6502.csv
"""


import pandas as pd

from src.parsers.fabric_db import get_fabric_db
from src.parsers.pins_parser import load_and_validate
from src.parsers.netlist_parser import parse_netlist
from src.placement.placer_rl import (
    run_greedy_sa_then_rl_pipeline,
    hpwl_of_nets,
    fixed_points_from_pins,
    nets_map_from_graph_df,
)


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

    # Load inputs (use merged fabric DB to ensure cell_x/cell_y columns exist)
    fabric, fabric_df = get_fabric_db(args.fabric_yaml, args.fabric_cells_yaml)
    pins_df, _pins_meta = load_and_validate(args.pins_yaml)
    logical_db, ports_df, netlist_graph = parse_netlist(args.design_json)

    # Run pipeline
    t_pipeline_start = time.perf_counter()
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

    # Determine output directory based on design name
    design_name = Path(args.design_json).stem.replace("_mapped", "")
    build_dir = Path("build") / design_name
    build_dir.mkdir(parents=True, exist_ok=True)

    # Write baseline placement to CSV for later inspection
    placement_out_path = build_dir / f"{design_name}_greedy_sa_placement.csv"
    placement_df.to_csv(placement_out_path, index=False)
    print(f"Saved Greedy+SA placement to: {placement_out_path}")

    nets = nets_map_from_graph_df(netlist_graph)
    fixed_pts = fixed_points_from_pins(updated_pins)

    t_hpwl_start = time.perf_counter()
    hpwl_before = baseline_sa_hpwl
    hpwl_after = hpwl_of_nets(nets, _pos_map_from_df(refined_df), fixed_pts)
    t_hpwl_end = time.perf_counter()

    delta = hpwl_after - hpwl_before
    pct = (delta / hpwl_before * 100.0) if hpwl_before > 0 else 0.0

    # Prepare refined placement output path before printing
    out_path = build_dir / f"{design_name}_ppo_refined_placement.csv"

    print("==== Greedy+SA Placement ====")
    print(f"Greedy+SA HPWL (all nets): {hpwl_before:.3f}")
    print(f"Placement CSV: {placement_out_path}")
    print("==== PPO Refined Placement ====")
    print(f"PPO Refined HPWL (all nets): {hpwl_after:.3f}")
    print(f"Refined CSV: {out_path}")
    print("==== Delta ====")
    print(f"ΔHPWL: {delta:.3f} ({pct:.2f}%)")
    print("==== Timing ====")
    print(f"Pipeline total: {t_pipeline_end - t_pipeline_start:.3f}s")
    print(f"HPWL compute: {t_hpwl_end - t_hpwl_start:.3f}s")

    # Save
    refined_df.to_csv(out_path, index=False)
    print(f"Saved refined placement to: {out_path}")


if __name__ == "__main__":
    main()
