import sys
import os
import datetime
from pathlib import Path

# Add src to path
project_root = Path(__file__).resolve().parent
sys.path.append(str(project_root))

from src.placement.placer import run_placement
from src.cts.htree_builder import run_eco_flow
from src.Visualization.cts_plotter import plot_cts_tree_interactive

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

def main():
    design_name = "z80"
    
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
    
        # 1. Run Placement (Greedy + SA)
        # This also generates the placement heatmap
        print("\n[Step 1] Running Placement...")
        run_placement(design_name)
        
        # 2. Run CTS & ECO
        print("\n[Step 2] Running CTS & ECO...")
        
        # Define paths
        netlist_path = str(project_root / f"inputs/designs/{design_name}_mapped.json")
        build_dir = project_root / "build" / design_name
        map_file_path = str(build_dir / f"{design_name}.map")
        fabric_cells_path = str(project_root / "inputs/Platform/fabric_cells.yaml")
        fabric_path = str(project_root / "inputs/Platform/fabric.yaml")
        pins_path = str(project_root / "inputs/Platform/pins.yaml")
        output_dir = str(build_dir)
        
        run_eco_flow(
            design_name=design_name,
            netlist_path=netlist_path,
            map_file_path=map_file_path,
            fabric_cells_path=fabric_cells_path,
            fabric_path=fabric_path,
            output_dir=output_dir,
            pins_path=pins_path
        )
        
        # 3. Visualize CTS
        print("\n[Step 3] Visualizing CTS...")
        cts_json_path = str(build_dir / f"{design_name}_cts.json")
        placement_csv_path = str(build_dir / f"{design_name}_placement.csv")
        cts_html_path = str(build_dir / f"{design_name}_cts.html")
        
        plot_cts_tree_interactive(
            placement_csv=placement_csv_path,
            fabric_cells_yaml=fabric_cells_path,
            cts_json=cts_json_path,
            output_path=cts_html_path,
            design_name=design_name
        )
        
        print(f"\n=== Flow Complete for {design_name} ===")
        print(f"Outputs are in: {build_dir}")
        print(f"  - Placement CSV: {design_name}_placement.csv")
        print(f"  - Placement Heatmap: {design_name}_placement_heatmap.png")
        print(f"  - CTS Visualization: {design_name}_cts.html")
        print(f"  - Final Verilog: {design_name}_final.v")
    
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
