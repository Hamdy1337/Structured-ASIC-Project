
import sys
import os
from pathlib import Path

# Add src to path
project_root = Path(".").resolve()
sys.path.append(str(project_root))

from src.cts.htree_builder import run_eco_flow

def main():
    design_name = "z80"
    netlist_path = str(project_root / f"inputs/designs/{design_name}_mapped.json")
    build_dir = project_root / "build" / design_name
    map_file_path = str(build_dir / f"{design_name}.map")
    fabric_cells_path = str(project_root / "inputs/Platform/fabric_cells.yaml")
    fabric_path = str(project_root / "inputs/Platform/fabric.yaml")
    pins_path = str(project_root / "inputs/Platform/pins.yaml")
    output_dir = str(build_dir)
    
    print("Running CTS debug...")
    run_eco_flow(
        design_name=design_name,
        netlist_path=netlist_path,
        map_file_path=map_file_path,
        fabric_cells_path=fabric_cells_path,
        fabric_path=fabric_path,
        output_dir=output_dir,
        pins_path=pins_path
    )
    print("CTS debug complete.")

if __name__ == "__main__":
    main()
