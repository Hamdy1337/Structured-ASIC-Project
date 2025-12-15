#!/usr/bin/env python3
"""
run_arith_flow.py: Run the complete placement and CTS flow for the arith design.

Usage:
    python run_arith_flow.py
"""

from datetime import datetime
from pathlib import Path
import sys

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from src.placement.placer import run_placement
from src.cts.htree_builder import run_eco_flow


def main():
    design_name = "arith"
    
    print(f"=== Starting Flow for {design_name} ===")
    print(f"Time: {datetime.now()}")
    print()
    
    # Step 1: Run Placement
    print("[Step 1] Running Placement...")
    run_placement(design_name)
    print()
    
    # Step 2: Run CTS/ECO Flow
    print("[Step 2] Running CTS and ECO Flow...")
    
    netlist_path = project_root / f"inputs/designs/{design_name}_mapped.json"
    map_file_path = project_root / "build" / design_name / f"{design_name}.map"
    fabric_cells_path = project_root / "inputs/Platform/fabric_cells.yaml"
    fabric_path = project_root / "inputs/Platform/fabric.yaml"
    pins_path = project_root / "inputs/Platform/pins.yaml"
    output_dir = project_root / "build" / design_name
    
    if not map_file_path.exists():
        print(f"Error: Map file not found: {map_file_path}")
        print("Placement may have failed.")
        return
    
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
    print(f"=== Flow Complete for {design_name} ===")
    print(f"Outputs in: build/{design_name}/")
    print(f"  - {design_name}.map (placement mapping)")
    print(f"  - {design_name}_eco.map (ECO mapping with CTS)")
    print(f"  - {design_name}_final.v (final netlist)")
    print(f"  - {design_name}_cts.json (CTS tree data)")
    print(f"  - {design_name}_placement_heatmap.png")


if __name__ == "__main__":
    main()
