#!/usr/bin/env python3
"""
run_6502_flow.py: Run the complete Phase 1-3 flow for the 6502 design.

Phases:
  Phase 1: Validation - Check if design fits on fabric
  Phase 2: Placement - Assign logical cells to physical slots (with slow SA)
  Phase 3: CTS & ECO - Clock tree synthesis and power-down ECO

Usage:
    python run_6502_flow.py
"""

import sys
import os
import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from src.validation.validator import validate_design, print_validation_report
from src.parsers.fabric_db import get_fabric_db
from src.parsers.netlist_parser import get_logical_db
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
    design_name = "6502"
    
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
        
        # ============================================================
        # PHASE 1: Validation
        # ============================================================
        print("[Phase 1] Validating Design...")
        print("  Checking if design fits on fabric...")
        
        # Load fabric database (available slots)
        _, fabric_db = get_fabric_db(
            str(project_root / "inputs" / "Platform" / "fabric.yaml"),
            str(project_root / "inputs" / "Platform" / "fabric_cells.yaml")
        )
        
        # Load logical database (required cells)
        logical_db = get_logical_db(
            str(project_root / "inputs" / "designs" / f"{design_name}_mapped.json")
        )
        
        # Validate design
        validation_result = validate_design(fabric_db, logical_db)
        
        # Print validation report
        print()
        print_validation_report(validation_result)
        print()
        
        if not validation_result.passed:
            print(f"ERROR: Design '{design_name}' cannot be implemented on this fabric!")
            print("Aborting flow.")
            sys.exit(1)
        
        print("[Phase 1] Validation PASSED!")
        print()
        
        # ============================================================
        # PHASE 2: Placement (with slow SA cooling for thorough optimization)
        # ============================================================
        print("[Phase 2] Running Placement...")
        print("  Using slow SA cooling (sa_moves_per_temp=30000, sa_cooling_rate=0.995)")
        run_placement(design_name, sa_moves_per_temp=30000, sa_cooling_rate=0.995, enable_sa_animation=True)
        print()
        
        # ============================================================
        # PHASE 3: CTS & ECO
        # ============================================================
        print("[Phase 3] Running CTS and ECO Flow...")
        
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
        
        # Visualize CTS (part of Phase 3)
        print()
        print("[Phase 3] Visualizing CTS...")
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
