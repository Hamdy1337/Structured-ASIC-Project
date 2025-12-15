#!/usr/bin/env python3
"""
run_arith_rl_flow.py: Run the complete placement and CTS flow for the arith design
using the RL-based placer (PPO) instead of the standard Greedy+SA placer.

Usage:
    python run_arith_rl_flow.py
"""

from datetime import datetime
from pathlib import Path
import sys

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from src.placement.placer_rl import run_greedy_sa_then_rl_pipeline
from src.cts.htree_builder import run_eco_flow
from src.parsers.fabric_parser import parse_fabric_file_cached
from src.parsers.fabric_cells_parser import load_fabric_cells
from src.parsers.pins_parser import load_and_validate as load_pins_df
from src.parsers.netlist_parser import NetlistParser
import pandas as pd


def main():
    design_name = "arith"
    
    print(f"=== Starting RL-based Flow for {design_name} ===")
    print(f"Time: {datetime.now()}")
    print()
    
    # Paths
    netlist_path = project_root / f"inputs/designs/{design_name}_mapped.json"
    fabric_cells_path = project_root / "inputs/Platform/fabric_cells.yaml"
    fabric_path = project_root / "inputs/Platform/fabric.yaml"
    pins_path = project_root / "inputs/Platform/pins.yaml"
    output_dir = project_root / "build" / design_name
    
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Load Data
    print("[Step 1] Loading design data...")
    
    # Load fabric
    fabric, _ = parse_fabric_file_cached(str(fabric_path))
    fabric_cells = load_fabric_cells(str(fabric_cells_path))
    
    # Build fabric_df from fabric_cells
    rows = []
    for tile_name, tile_info in fabric_cells.tiles.items():
        for cell in tile_info.cells:
            rows.append({
                'tile_name': tile_name,
                'cell_x': tile_info.origin[0] + cell.offset[0],
                'cell_y': tile_info.origin[1] + cell.offset[1],
                'cell_type': cell.cell_type,
                'template_name': cell.template_name,
            })
    fabric_df = pd.DataFrame(rows)
    print(f"  - Loaded {len(fabric_df)} fabric cells")
    
    # Load pins
    pins_df, pins_meta = load_pins_df(str(pins_path))
    print(f"  - Loaded {len(pins_df)} pins")
    
    # Load netlist
    parser = NetlistParser(str(netlist_path))
    netlist_graph = parser.build_graph()
    print(f"  - Loaded netlist with {len(netlist_graph)} connections")
    
    # Get ports_df from pins_df (ports are pins with port_name)
    ports_df = pins_df[pins_df['port_name'].notna()].copy() if 'port_name' in pins_df.columns else pd.DataFrame()
    
    # Step 2: Run RL-based Placement Pipeline
    print()
    print("[Step 2] Running RL-based Placement (Greedy+SA + PPO Refinement)...")
    print("  - This will train PPO agents for placement optimization")
    print("  - Training may take several minutes depending on design size")
    print()
    
    # Run the RL pipeline
    # Returns: (updated_pins, greedy_sa_placement_df, rl_refined_placement_df, baseline_hpwl)
    result = run_greedy_sa_then_rl_pipeline(
        fabric=fabric,
        fabric_df=fabric_df,
        pins_df=pins_df,
        ports_df=ports_df,
        netlist_graph=netlist_graph,
        max_action_full=512,           # Max candidates for full placer
        full_placer_train_eps=50,      # Training episodes for full placer
        swap_refine_train_eps=100,     # Training episodes for swap refiner
        batch_size=64,                 # Batch size for swap refiner
        device="cpu",                  # Use "cuda" if GPU available
        max_train_batches=30,          # Limit training batches
        max_apply_batches=50,          # Limit application batches
        full_steps_per_ep=256,         # Steps per episode (full placer)
        swap_steps_per_ep=50,          # Steps per episode (swap refiner)
        enable_timing=True,            # Print timing info
        validate_final=True,           # Run final validation
        sa_moves_per_temp=5000,        # SA moves per temperature
    )
    
    # Unpack results
    if isinstance(result, tuple) and len(result) == 4:
        updated_pins, greedy_sa_placement_df, rl_placement_df, baseline_hpwl = result
    else:
        # Fallback if only placement_df returned
        rl_placement_df = result
        updated_pins = pins_df
        greedy_sa_placement_df = rl_placement_df
        baseline_hpwl = 0.0
    
    print()
    print(f"  - Greedy+SA HPWL: {baseline_hpwl:.3f}")
    print(f"  - Placed {len(rl_placement_df)} cells")
    
    # Save placement map
    map_file_path = output_dir / f"{design_name}.map"
    with open(map_file_path, 'w') as f:
        for row in rl_placement_df.itertuples(index=False):
            # Format: logical_name physical_slot_name
            # Need to convert cell position to physical slot name
            # Physical slot name format: T{x}Y{y}__{template}
            tile_x = int(row.x_um // 27.6)  # Approximate tile size
            tile_y = int(row.y_um // 13.0)
            physical_name = f"T{tile_x}Y{tile_y}__{row.cell_name.split('_')[-1] if '_' in row.cell_name else row.cell_name}"
            f.write(f"{row.cell_name} {physical_name}\n")
    print(f"  - Saved placement map to {map_file_path}")
    
    # Step 3: Run CTS/ECO Flow
    print()
    print("[Step 3] Running CTS and ECO Flow...")
    
    run_eco_flow(
        design_name=design_name,
        netlist_path=str(netlist_path),
        map_file_path=str(map_file_path),
        fabric_cells_path=str(fabric_cells_path),
        fabric_path=str(fabric_path),
        output_dir=str(output_dir),
        pins_path=str(pins_path),
    )
    
    print()
    print(f"=== RL Flow Complete for {design_name} ===")
    print(f"Outputs in: build/{design_name}/")
    print(f"  - {design_name}.map (RL-refined placement mapping)")
    print(f"  - {design_name}_eco.map (ECO mapping with CTS)")
    print(f"  - {design_name}_final.v (final netlist)")
    print(f"  - {design_name}_cts.json (CTS tree data)")


if __name__ == "__main__":
    main()
