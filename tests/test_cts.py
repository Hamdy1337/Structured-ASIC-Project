
import sys
import os
import json
import re
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from src.cts.htree_builder import run_eco_flow

def verify_power_down_eco(verilog_path: Path, netlist_path: Path, design_name: str):
    """Verify Power-Down ECO was implemented correctly."""
    print("\n" + "="*60)
    print("Verifying Power-Down ECO...")
    print("="*60)
    
    # Check 1: Verify tie nets exist in Verilog
    with open(verilog_path, 'r') as f:
        verilog_content = f.read()
    
    tie_low_found = 'tie_low_net' in verilog_content
    tie_high_found = 'tie_high_net' in verilog_content
    
    print(f"✓ Tie-low net found: {tie_low_found}")
    print(f"✓ Tie-high net found: {tie_high_found}")
    
    if not tie_low_found or not tie_high_found:
        print("ERROR: Tie nets not found in Verilog!")
        return False
    
    # Check 2: Verify conb_1 cell exists
    conb_pattern = r'sky130_fd_sc_hd__conb_1\s+(\w+)\s*\([^)]*\.LO\(tie_low_net\)[^)]*\.HI\(tie_high_net\)'
    conb_match = re.search(conb_pattern, verilog_content)
    
    if conb_match:
        tie_cell_name = conb_match.group(1)
        print(f"✓ Tie cell found: {tie_cell_name}")
    else:
        print("WARNING: Could not find conb_1 cell with both LO and HI outputs")
        # Try simpler pattern
        if 'sky130_fd_sc_hd__conb_1' in verilog_content:
            print("  (conb_1 cell exists but pattern matching failed)")
    
    # Check 3: Count unused cells in Verilog
    unused_cell_pattern = r'sky130_fd_sc_hd__\w+\s+unused_\w+'
    unused_cells = re.findall(unused_cell_pattern, verilog_content)
    print(f"✓ Found {len(unused_cells)} unused cells in Verilog")
    
    # Check 4: Verify some unused cells have tied inputs
    if unused_cells:
        # Sample a few unused cells and check their connections
        sample_cells = unused_cells[:min(5, len(unused_cells))]
        print(f"\n  Sampling {len(sample_cells)} unused cells:")
        
        for cell_line in sample_cells:
            # Extract cell name
            cell_match = re.search(r'unused_(\w+)', cell_line)
            if cell_match:
                cell_name = cell_match.group(0)
                # Find the full cell instantiation
                cell_pattern = rf'{re.escape(cell_name)}\s*\([^)]+\)'
                cell_inst = re.search(cell_pattern, verilog_content)
                if cell_inst:
                    inst_text = cell_inst.group(0)
                    # Check if it's connected to tie nets
                    has_tie_low = 'tie_low_net' in inst_text
                    has_tie_high = 'tie_high_net' in inst_text
                    if has_tie_low or has_tie_high:
                        print(f"    ✓ {cell_name}: Properly tied")
                    else:
                        print(f"    ✗ {cell_name}: No tie connections found!")
    
    # Check 5: Verify netlist JSON has tie nets
    try:
        with open(netlist_path, 'r') as f:
            netlist_data = json.load(f)
        
        # Find top module
        top_module = None
        if design_name in netlist_data.get("modules", {}):
            top_module = netlist_data["modules"][design_name]
        elif "sasic_top" in netlist_data.get("modules", {}):
            top_module = netlist_data["modules"]["sasic_top"]
        
        if top_module:
            netnames = top_module.get('netnames', {})
            has_tie_low_net = 'tie_low_net' in netnames
            has_tie_high_net = 'tie_high_net' in netnames
            
            print(f"\n✓ Tie nets in netlist JSON:")
            print(f"    tie_low_net: {has_tie_low_net}")
            print(f"    tie_high_net: {has_tie_high_net}")
            
            # Count unused cells in netlist
            cells = top_module.get('cells', {})
            unused_cells_in_netlist = [name for name in cells.keys() if name.startswith('unused_')]
            print(f"✓ Unused cells in netlist JSON: {len(unused_cells_in_netlist)}")
            
            # Check tie cell exists
            tie_cells = [name for name in cells.keys() if name.startswith('tie_cell_')]
            print(f"✓ Tie cells in netlist JSON: {len(tie_cells)}")
            if tie_cells:
                print(f"    Tie cell name: {tie_cells[0]}")
    except Exception as e:
        print(f"WARNING: Could not verify netlist JSON: {e}")
    
    print("\n" + "="*60)
    print("Power-Down ECO Verification Complete")
    print("="*60 + "\n")
    
    return True

def test_cts():
    design_name = "6502"
    
    # Define paths based on project structure
    netlist_path = project_root / "inputs" / "designs" / f"{design_name}_mapped.json"
    
    # Use the .map file (logical -> physical mapping from placement)
    map_file_path = project_root / "build" / design_name / f"{design_name}.map"
    
    fabric_cells_path = project_root / "inputs" / "Platform" / "fabric_cells.yaml"
    fabric_path = project_root / "inputs" / "Platform" / "fabric.yaml"
    output_dir = project_root / "build" / design_name

    print(f"Testing ECO Flow for {design_name}")
    print(f"Netlist: {netlist_path}")
    print(f"Map file: {map_file_path}")
    
    if not map_file_path.exists():
        print(f"Error: Map file not found at {map_file_path}")
        print("ERROR: No .map file found. Please run placement first to generate the .map file.")
        return

    # Run ECO Flow
    run_eco_flow(
        design_name=design_name,
        netlist_path=str(netlist_path),
        map_file_path=str(map_file_path),
        fabric_cells_path=str(fabric_cells_path),
        fabric_path=str(fabric_path),
        output_dir=str(output_dir)
    )
    
    # Comprehensive Validation
    print("\n" + "="*80)
    print("RUNNING COMPREHENSIVE VALIDATION")
    print("="*80)
    
    from src.validation.eco_validator import validate_eco_flow, print_validation_report
    
    verilog_path = output_dir / f"{design_name}_final.v"
    cts_json_path = output_dir / f"{design_name}_cts.json"
    
    if verilog_path.exists():
        # Run comprehensive validation
        validation_result = validate_eco_flow(
            netlist_path=netlist_path,
            verilog_path=verilog_path,
            cts_json_path=cts_json_path,
            map_file_path=map_file_path,
            design_name=design_name
        )
        
        print_validation_report(validation_result)
        
        # Also run original Power-Down ECO check
        verify_power_down_eco(verilog_path, netlist_path, design_name)
    else:
        print(f"ERROR: Verilog file not found at {verilog_path}")
    
    # Run Visualization
    print("\nRunning Visualization...")
    import subprocess
    cts_json_path = output_dir / f"{design_name}_cts.json"
    output_image_path = output_dir / "6502_cts_tree.html"
    
    # For visualization, we need a placement CSV with coordinates
    # Try to find it, or skip visualization if not found
    placement_csv_path = project_root / "build" / f"{design_name}.{design_name}_mapped.greedy_sa_placement.csv"
    if not placement_csv_path.exists():
        placement_csv_path = project_root / "build" / design_name / f"{design_name}_placement.csv"
    
    if cts_json_path.exists() and placement_csv_path.exists():
        cmd = [
            sys.executable,
            str(project_root / "src" / "Visualization" / "cts_plotter.py"),
            "cts",
            "--placement", str(placement_csv_path),
            "--cts_data", str(cts_json_path),
            "--fabric_cells", str(fabric_cells_path),
            "--output", str(output_image_path),
            "--design", design_name
        ]
        
        try:
            subprocess.check_call(cmd)
            print(f"Visualization completed: {output_image_path}")
        except subprocess.CalledProcessError as e:
            print(f"WARNING: Visualization failed: {e}")
    else:
        if not cts_json_path.exists():
            print(f"WARNING: CTS JSON not found at {cts_json_path}, skipping visualization")
        if not placement_csv_path.exists():
            print(f"WARNING: Placement CSV not found at {placement_csv_path}, skipping visualization")

if __name__ == "__main__":
    test_cts()
