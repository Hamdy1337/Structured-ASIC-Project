import argparse
import os
from pathlib import Path
from typing import Dict, Tuple

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
    ap.add_argument("--design-json", default="inputs/designs/arith_mapped.json", help="Path to [design]_mapped.json")
    ap.add_argument("--fabric-yaml", default="inputs/Platform/fabric.yaml", help="Path to fabric.yaml")
    ap.add_argument("--pins-yaml", default="inputs/Platform/pins.yaml", help="Path to pins.yaml")
    ap.add_argument("--fabric-cells-yaml", default="inputs/Platform/fabric_cells.yaml", help="Path to fabric_cells.yaml")
    ap.add_argument("--max-action-full", type=int, default=1024)
    ap.add_argument("--full-placer-eps", type=int, default=0, help="PPO episodes for full placer (0 = skip training)")
    ap.add_argument("--swap-train-eps", type=int, default=100, help="PPO episodes for swap refiner per-batch")
    ap.add_argument("--swap-steps-per-ep", type=int, default=80, help="Environment steps per swap episode")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda", "mps"])
    ap.add_argument("--max-train-batches", type=int, default=50, help="Max training batches for swap PPO")
    ap.add_argument("--max-apply-batches", type=int, default=None, help="Max batches to apply refinement; default all")
    ap.add_argument("--full-steps-per-ep", type=int, default=512, help="Steps per episode for full placer")
    ap.add_argument("--out-csv", default="build/ppo_refined_placement.csv")
    args = ap.parse_args()

    # Load inputs (use merged fabric DB to ensure cell_x/cell_y columns exist)
    fabric, fabric_df = get_fabric_db(args.fabric_yaml, args.fabric_cells_yaml)
    pins_df, _pins_meta = load_and_validate(args.pins_yaml)
    logical_db, ports_df, netlist_graph = parse_netlist(args.design_json)

    # Run pipeline
    refined_df = run_greedy_sa_then_rl_pipeline(
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
    )

    # Compute HPWL before/after
    # Greedy+SA HPWL can be approximated by re-running Greedy+SA part inside pipeline,
    # but we can reconstruct from refined_df vs initial Greedy+SA placement by running Greedy+SA once.
    # For simplicity, compute HPWL on refined_df and report it; if the placement_df from Greedy+SA is needed,
    # you can modify the pipeline to return both. Here, we recompute Greedy+SA quickly:
    from src.placement.placer import place_cells_greedy_sim_anneal

    updated_pins, placement_df = place_cells_greedy_sim_anneal(
        fabric, fabric_df, pins_df, ports_df, netlist_graph
    )

    nets = nets_map_from_graph_df(netlist_graph)
    fixed_pts = fixed_points_from_pins(updated_pins)

    hpwl_before = hpwl_of_nets(nets, _pos_map_from_df(placement_df), fixed_pts)
    hpwl_after = hpwl_of_nets(nets, _pos_map_from_df(refined_df), fixed_pts)

    delta = hpwl_after - hpwl_before
    pct = (delta / hpwl_before * 100.0) if hpwl_before > 0 else 0.0

    print("HPWL Summary (all nets):")
    print(f"  Before (Greedy+SA): {hpwl_before:.3f}")
    print(f"  After  (PPO refine): {hpwl_after:.3f}")
    print(f"  Delta: {delta:.3f}  ({pct:.2f}%)")

    # Save
    out_path = Path(args.out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    refined_df.to_csv(out_path, index=False)
    print(f"Saved refined placement to: {out_path}")


if __name__ == "__main__":
    main()
