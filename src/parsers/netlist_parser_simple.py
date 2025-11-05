"""
SIMPLE STEP-BY-STEP VERSION - For Learning
==========================================

This is a simplified, educational version of the netlist parser.
It shows step-by-step how to parse the JSON file and build the data structures.

Read through this file section by section to understand:
1. What the JSON file structure looks like
2. How we extract cells and group them by type (logical_db)
3. How we build the connectivity graph (netlist_graph)
"""

import json


def parse_netlist_simple(json_file_path: str):
    """
    Step-by-step parser for Yosys JSON netlist files.
    
    This function shows you HOW to parse, bit by bit.
    """
    
    # ============================================================
    # STEP 1: Load the JSON file
    # ============================================================
    print("STEP 1: Loading JSON file...")
    with open(json_file_path, 'r') as f:
        data = json.load(f)
    
    # The JSON structure looks like:
    # {
    #   "creator": "Yosys ...",
    #   "modules": {
    #     "sasic_top": {
    #       "ports": {...},      # I/O pins (clk, rst_n, etc.)
    #       "cells": {...},      # All the logic cells (NANDs, DFFs, etc.)
    #       "netnames": {...}   # Signal names
    #     }
    #   }
    # }
    
    print(f"  ✓ Loaded JSON file with {len(data.get('modules', {}))} modules")
    
    
    # ============================================================
    # STEP 2: Find the top module (the main design)
    # ============================================================
    print("\nSTEP 2: Finding top module...")
    
    # The top module is usually called "sasic_top" or has a "top" attribute
    top_module_name = None
    
    # Look through all modules
    for module_name, module_data in data['modules'].items():
        # Check if this module has the "top" attribute set
        attrs = module_data.get('attributes', {})
        if attrs.get('top') == '00000000000000000000000000000001':
            top_module_name = module_name
            break
    
    # If not found, try common names
    if not top_module_name:
        for name in ['sasic_top', 'top']:
            if name in data['modules']:
                top_module_name = name
                break
    
    if not top_module_name:
        # Just use the first module
        top_module_name = list(data['modules'].keys())[0]
    
    print(f"  ✓ Found top module: {top_module_name}")
    
    # Get the data for the top module
    top_module = data['modules'][top_module_name]
    
    
    # ============================================================
    # STEP 3: Parse cells to create logical_db
    # ============================================================
    print("\nSTEP 3: Building logical_db (cells grouped by type)...")
    
    # logical_db structure: {cell_type: [list of cell instance names]}
    # Example: {"sky130_fd_sc_hd__nand2_1": ["cell1", "cell2", ...]}
    logical_db = {}
    
    # Get all cells from the top module
    cells = top_module.get('cells', {})
    print(f"  ✓ Found {len(cells)} total cells")
    
    # Loop through each cell
    for cell_name, cell_info in cells.items():
        # Each cell has:
        #   - name: the instance name (e.g., "$abc$1234")
        #   - type: the cell type (e.g., "sky130_fd_sc_hd__nand2_1")
        #   - connections: which nets it's connected to
        
        cell_type = cell_info.get('type', 'UNKNOWN')
        
        # Initialize list for this cell type if we haven't seen it before
        if cell_type not in logical_db:
            logical_db[cell_type] = []
        
        # Add this cell instance to the list for its type
        logical_db[cell_type].append(cell_name)
    
    print(f"  ✓ Grouped cells into {len(logical_db)} different types")
    print("  ✓ Example cell types found:")
    for i, (cell_type, cell_list) in enumerate(list(logical_db.items())[:5]):
        print(f"      {cell_type}: {len(cell_list)} instances")
    
    # ============================================================
    # STEP 4: Build netlist_graph (connectivity information)
    # ============================================================
    print("\nSTEP 4: Building netlist_graph (connectivity)...")
    
    # netlist_graph structure: Shows which cells are connected to which nets
    # We'll create two useful views:
    #   1. cell_to_nets: For each cell, which nets it's connected to
    #   2. net_to_cells: For each net, which cells are connected to it
    
    cell_to_nets = {}  # {cell_name: {port_name: [net_bits]}}
    net_to_cells = {}  # {net_bit: [list of cell names]}
    
    # Loop through cells again, this time to get connections
    for cell_name, cell_info in cells.items():
        # Get the connections for this cell
        connections = cell_info.get('connections', {})
        # connections looks like: {"A": [24], "Y": [124]}
        # This means: port A is connected to net 24, port Y to net 124
        
        # Store connections for this cell
        cell_to_nets[cell_name] = connections
        
        # Also build the reverse mapping: for each net, which cells use it
        for port_name, net_bits in connections.items():
            # net_bits is a list like [24] or [125, 126]
            if not isinstance(net_bits, list):
                net_bits = [net_bits]
            
            # For each net bit, add this cell to the list
            for net_bit in net_bits:
                if net_bit is not None:  # Skip None/null
                    if net_bit not in net_to_cells:
                        net_to_cells[net_bit] = []
                    if cell_name not in net_to_cells[net_bit]:
                        net_to_cells[net_bit].append(cell_name)
    
    print(f"  ✓ Built connectivity for {len(cell_to_nets)} cells")
    print(f"  ✓ Found {len(net_to_cells)} unique nets")
    
    # ============================================================
    # STEP 5: Package everything into netlist_graph
    # ============================================================
    print("\nSTEP 5: Packaging results...")
    
    # Create the netlist_graph dictionary
    netlist_graph = {
        'cells': cell_to_nets,           # Cell connections
        'net_to_cells': net_to_cells,    # Net connections
        'ports': top_module.get('ports', {}),  # I/O ports
        'netnames': top_module.get('netnames', {})  # Signal names
    }
    
    print("  ✓ Done!")
    
    # ============================================================
    # STEP 6: Return both data structures
    # ============================================================
    return logical_db, netlist_graph


# ============================================================
# EXAMPLE USAGE
# ============================================================
if __name__ == "__main__":
    # Example: Parse a design file
    json_path = "inputs/designs/arith_mapped.json"
    
    print("=" * 60)
    print("SIMPLE NETLIST PARSER - STEP BY STEP")
    print("=" * 60)
    
    logical_db, netlist_graph = parse_netlist_simple(json_path)
    
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    
    print("\n1. LOGICAL_DB (cells grouped by type):")
    print("   This is what the validator uses to check if we have enough fabric cells")
    total_cells = 0
    for cell_type, cell_list in sorted(logical_db.items()):
        print(f"   {cell_type}: {len(cell_list)} instances")
        total_cells += len(cell_list)
    print(f"\n   Total: {total_cells} cells")
    
    print("\n2. NETLIST_GRAPH:")
    print("   This is what the placer uses to understand connectivity")
    print(f"   - {len(netlist_graph['cells'])} cells with connections")
    print(f"   - {len(netlist_graph['net_to_cells'])} unique nets")
    
    print("\n3. EXAMPLE CONNECTION:")
    # Show an example: pick the first cell
    if netlist_graph['cells']:
        first_cell = list(netlist_graph['cells'].keys())[0]
        connections = netlist_graph['cells'][first_cell]
        print(f"   Cell '{first_cell}' is connected to:")
        for port, nets in list(connections.items())[:3]:
            print(f"      Port {port}: nets {nets}")

