#!/usr/bin/env python3
"""
run_arith_flow.py: Run the complete placement and CTS flow for the arith design.

Usage:
    python run_arith_flow.py
"""

import sys
import os
import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from src.placement.placer import run_placement
from src.cts.htree_builder import run_eco_flow
from src.Visualization.cts_plotter import plot_cts_tree_interactive


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
    design_name = "arith"
    
    # Setup Logging
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = log_dir / f"{design_name}_flow_{timestamp}.log"
    
    print(f"Logging to: {log_file_path}")
    
    f_log = open(log_file_path, 'w', encoding='utf-8')
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    sys.stdout = Tee(original_stdout, f_log)
    sys.stderr = Tee(original_stderr, f_log)
    
    try:
        print(f"=== Starting Flow for {design_name} ===")
        print(f"Time: {datetime.datetime.now()}")
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
        
        # Step 3: Visualize CTS
        print()
        print("[Step 3] Visualizing CTS...")
        cts_json_path = output_dir / f"{design_name}_cts.json"
        placement_csv_path = output_dir / f"{design_name}_placement.csv"
        cts_html_path = output_dir / f"{design_name}_cts.html"
        
        if cts_json_path.exists() and placement_csv_path.exists():
            plot_cts_tree_interactive(
                placement_csv=str(placement_csv_path),
                fabric_cells_yaml=str(fabric_cells_path),
                cts_json=str(cts_json_path),
                output_path=str(cts_html_path),
                design_name=design_name
            )
        else:
            print(f"[WARNING] CTS visualization skipped - missing files")
        
        print()
        print(f"=== Flow Complete for {design_name} ===")
        print(f"Outputs in: build/{design_name}/")
        print(f"  - {design_name}.map (placement mapping)")
        print(f"  - {design_name}_eco.map (ECO mapping with CTS)")
        print(f"  - {design_name}_final.v (final netlist)")
        print(f"  - {design_name}_cts.json (CTS tree data)")
        print(f"  - {design_name}_placement_heatmap.png")
        print(f"  - {design_name}_cts.html (CTS visualization)")
    
    except Exception as e:
        print(f"\n❌ ERROR: Flow failed with exception: {e}")
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
