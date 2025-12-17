#!/usr/bin/env python3
"""
run_6502_rl_flow.py: Run the complete placement and CTS flow for the 6502 design
using the RL-based placer (PPO) instead of the standard Greedy+SA placer.

This flow includes:
1. Greedy+SA initial placement
2. PPO Full Placer training and application
3. PPO Swap Refiner training and application
4. CTS (Clock Tree Synthesis) on the RL-refined placement
5. ECO (Power-down) and final Verilog generation

Animation is captured throughout the RL placement phases.

Usage:
    python run_6502_rl_flow.py
"""

import datetime
from pathlib import Path
import sys

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from src.placement.placer_rl import run_greedy_sa_then_rl_pipeline
from src.cts.htree_builder import run_eco_flow
from src.parsers.fabric_parser import parse_fabric_file_cached
from src.parsers.fabric_cells_parser import parse_fabric_cells_file
from src.parsers.pins_parser import load_and_validate as load_pins_df
from src.parsers.netlist_parser import NetlistParser
import pandas as pd


class Tee:
    """Capture output to both stdout/stderr and a log file."""
    def __init__(self, *files):
        self.files = files

    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()


def main():
    design_name = "6502"
    
    # Setup Logging
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = log_dir / f"{design_name}_rl_flow_{timestamp}.log"
    
    print(f"Logging to: {log_file_path}")
    
    f_log = open(log_file_path, 'w', encoding='utf-8')
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    sys.stdout = Tee(original_stdout, f_log)
    sys.stderr = Tee(original_stderr, f_log)
    
    try:
        print(f"{'='*60}")
        print(f"=== Starting RL-based Flow for {design_name} ===")
        print(f"=== Hybrid: Greedy+SA + PPO Refinement ===")
        print(f"{'='*60}")
        print(f"Time: {datetime.datetime.now()}")
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
        fabric_cells, fabric_cells_df = parse_fabric_cells_file(str(fabric_cells_path))
        
        # Build fabric_df from fabric_cells with ABSOLUTE coordinates (tile.x + cell.x)
        # This is used for the RL placer and for mapping placement to physical cells
        rows = []
        for tile_name, tile_info in fabric_cells.tiles.items():
            for cell in tile_info.cells:
                rows.append({
                    'tile_name': tile_name,
                    'cell_x': tile_info.x + cell.x,  # Absolute X coordinate
                    'cell_y': tile_info.y + cell.y,  # Absolute Y coordinate
                    'cell_name': cell.name,          # Full physical name (e.g., "T0Y3__R0_TAP_0")
                    'cell_orient': cell.orient,
                })
        fabric_df = pd.DataFrame(rows)
        print(f"  - Loaded {len(fabric_df)} fabric cells")
        
        # Load pins
        pins_df, pins_meta = load_pins_df(str(pins_path))
        print(f"  - Loaded {len(pins_df)} pins")
        
        # Load netlist
        parser = NetlistParser(str(netlist_path))
        logical_db, ports_df, netlist_graph = parser.parse()
        print(f"  - Loaded netlist with {len(netlist_graph)} connections")
        print(f"  - Found {len(logical_db)} cells, {len(ports_df)} ports")
        
        # Step 2: Run RL-based Placement Pipeline
        print()
        print("[Step 2] Running RL-based Placement (Greedy+SA + PPO Refinement)...")
        print("  - Phase 1: Greedy+SA initial placement")
        print("  - Phase 2: PPO Full Placer training and application")
        print("  - Phase 3: PPO Swap Refiner training and application")
        print("  - Animation: Capturing frames at each stage")
        print()
        
        # Animation paths
        anim_frames_dir = output_dir / "rl_placement_animation_frames"
        anim_output_path = output_dir / f"{design_name}_rl_placement_animation.mp4"
        
        # Run the RL pipeline (EXTENDED TRAINING - target ~6 hours runtime)
        # Based on measured timing: 1 swap batch (1000 eps) ≈ 1.7 hours
        result = run_greedy_sa_then_rl_pipeline(
            fabric=fabric,
            fabric_df=fabric_df,
            pins_df=pins_df,
            ports_df=ports_df,
            netlist_graph=netlist_graph,
            max_action_full=1024,          # Larger action space
            full_placer_train_eps=0,     # 500 full placer training episodes (~30 min)
            swap_refine_train_eps=120,    # 1000 swap episodes per batch
            batch_size=128,                # Larger batch size
            device="cpu",
            max_train_batches=3,           # FIXED: Only 3 swap batches (~5 hours)
            max_apply_batches=10,          # FIXED: Reduced from 300 to 10
            full_steps_per_ep=512,         # More steps per full placer episode
            swap_steps_per_ep=200,         # More swap steps per episode
            enable_timing=True,
            validate_final=True,
            sa_moves_per_temp=30000,       # 3x more SA moves for better initial placement
            sa_cooling_rate=0.995,         # SLOW cooling: 0.995 vs default 0.95
            animation_enabled=True,
            animation_frames_dir=str(anim_frames_dir),
            output_animation_path=str(anim_output_path),
            design_name=design_name,
        )
        
        # Unpack results
        if isinstance(result, tuple) and len(result) == 4:
            updated_pins, greedy_sa_placement_df, rl_placement_df, baseline_hpwl = result
        else:
            rl_placement_df = result
            updated_pins = pins_df
            greedy_sa_placement_df = rl_placement_df
            baseline_hpwl = 0.0
        
        print()
        print(f"[Step 2 Complete]")
        print(f"  - Greedy+SA HPWL: {baseline_hpwl:.3f}")
        print(f"  - Placed {len(rl_placement_df)} cells (RL-refined)")
        
        # Step 3: Save RL-refined placement map
        print()
        print("[Step 3] Saving RL-refined placement map...")
        
        from src.placement.placement_mapper import map_placement_to_physical_cells, generate_map_file
        
        placement_with_physical = map_placement_to_physical_cells(
            placement_df=rl_placement_df,
            fabric_cells_df=fabric_df,  # Use fabric_df with absolute coords (cell_x = tile.x + cell.x)
            fabric_df=fabric_df,
        )
        
        # Save RL placement CSV (for analysis/comparison with SA)
        rl_csv_path = output_dir / f"{design_name}_rl_placement.csv"
        rl_placement_df.to_csv(rl_csv_path, index=False)
        print(f"  - Saved RL placement to {rl_csv_path}")
        
        map_file_path = output_dir / f"{design_name}_rl.map"
        generate_map_file(
            placement_df=placement_with_physical,
            map_file_path=map_file_path,
            design_name=design_name,
        )
        print(f"  - Mapped {len(placement_with_physical)} cells")
        
        # Step 4: Run CTS/ECO Flow on RL-refined placement
        print()
        print("[Step 4] Running CTS and ECO Flow on RL-refined placement...")
        
        run_eco_flow(
            design_name=design_name,
            netlist_path=str(netlist_path),
            map_file_path=str(map_file_path),
            fabric_cells_path=str(fabric_cells_path),
            fabric_path=str(fabric_path),
            output_dir=str(output_dir),
            pins_path=str(pins_path),
            output_prefix="_rl",
        )
        
        print()
        print(f"{'='*60}")
        print(f"=== RL Flow Complete for {design_name} ===")
        print(f"{'='*60}")
        print(f"Outputs in: build/{design_name}/")
        print(f"  - {design_name}_rl.map (RL-refined placement mapping)")
        print(f"  - {design_name}_rl_eco.map (ECO mapping with CTS)")
        print(f"  - {design_name}_rl_final.v (final netlist)")
        print(f"  - {design_name}_cts.json (CTS tree data)")
        print(f"  - {design_name}_rl_placement_animation.mp4 (RL placement animation)")
        print(f"  - {design_name}_rl_placement_animation.gif (GIF version)")
        print()
        print("To run routing, use:")
        print("  .\\debug_route_6502_rl.ps1")
        print()
    
    except Exception as e:
        print(f"\n❌ ERROR: RL Flow failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    finally:
        # Restore stdout/stderr
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        f_log.close()
        print(f"Log saved to: {log_file_path}")


if __name__ == "__main__":
    main()
