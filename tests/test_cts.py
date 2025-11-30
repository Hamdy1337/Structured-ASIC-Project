
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from src.cts.htree_builder import run_eco_flow

def test_cts():
    design_name = "6502"
    
    # Define paths based on project structure
    netlist_path = project_root / "inputs" / "designs" / f"{design_name}_mapped.json"
    
    # Use the placement file found in build
    # Note: The file name might vary based on previous runs. 
    # I saw "6502.6502_mapped.greedy_sa_placement.csv" in build/
    placement_path = project_root / "build" / f"{design_name}.{design_name}_mapped.greedy_sa_placement.csv"
    
    fabric_cells_path = project_root / "inputs" / "Platform" / "fabric_cells.yaml"
    fabric_path = project_root / "inputs" / "Platform" / "fabric.yaml"
    output_dir = project_root / "build" / design_name

    print(f"Testing ECO Flow for {design_name}")
    print(f"Netlist: {netlist_path}")
    print(f"Placement: {placement_path}")
    
    if not placement_path.exists():
        print(f"Error: Placement file not found at {placement_path}")
        # Try alternative path if the specific one doesn't exist
        alt_path = project_root / "build" / design_name / f"{design_name}_placement.csv"
        if alt_path.exists():
            print(f"Found alternative placement: {alt_path}")
            placement_path = alt_path
        else:
            return

    run_eco_flow(
        design_name=design_name,
        netlist_path=str(netlist_path),
        placement_path=str(placement_path),
        fabric_cells_path=str(fabric_cells_path),
        fabric_path=str(fabric_path),
        output_dir=str(output_dir)
    )
    
    # Run Visualization
    print("Running Visualization...")
    import subprocess
    cts_json_path = output_dir / f"{design_name}_cts.json"
    output_image_path = output_dir / "6502_cts_tree.html"
    
    cmd = [
        sys.executable,
        str(project_root / "src" / "visualization" / "cts_plotter.py"),
        "cts",
        "--placement", str(placement_path),
        "--cts_data", str(cts_json_path),
        "--fabric_cells", str(fabric_cells_path),
        "--output", str(output_image_path),
        "--design", design_name
    ]
    
    subprocess.check_call(cmd)
    print(f"Visualization completed: {output_image_path}")

if __name__ == "__main__":
    test_cts()
