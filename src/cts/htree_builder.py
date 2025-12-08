"""
htree_builder.py: Generates ECO netlist with H-tree Clock Tree Synthesis (CTS).
"""

import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional, Any
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

# Add src to path to allow imports
sys.path.append(str(Path(__file__).parent.parent))

from src.parsers.fabric_cells_parser import parse_fabric_cells_file
from src.parsers.fabric_db import get_fabric_db
from src.parsers.netlist_parser import NetlistParser
from src.placement.placement_mapper import map_placement_to_physical_cells
from src.parsers.pins_parser import load_and_validate as load_pins_df

def parse_map_file(map_file_path: str) -> pd.DataFrame:
    """
    Parse a .map file to get logical to physical cell name mappings.
    
    Format:
        # Comments
        logical_cell_name physical_cell_name
    
    Args:
        map_file_path: Path to the .map file
    
    Returns:
        DataFrame with columns ['cell_name', 'physical_cell_name']
    """
    mappings = []
    with open(map_file_path, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue
            # Parse: logical_name physical_name
            parts = line.split()
            if len(parts) >= 2:
                logical_name = parts[0]
                physical_name = parts[1]
                mappings.append({
                    'cell_name': logical_name,
                    'physical_cell_name': physical_name
                })
    
    return pd.DataFrame(mappings)

@dataclass
class TreeNode:
    x: float
    y: float
    cell_name: str  # Physical cell name for buffers, Logical cell name for sinks
    is_sink: bool
    children: List['TreeNode'] = field(default_factory=list)
    level: int = 0
    physical_name: str = "" # The physical name of the buffer used

class VerilogWriter:
    """Simple Verilog writer for the modified netlist."""
    def __init__(self, module_name: str, ports: Dict, cells: Dict, netnames: Dict):
        self.module_name = module_name
        self.ports = ports
        self.cells = cells
        self.netnames = netnames
        
    def generate(self) -> str:
        lines = []
        lines.append(f"module {self.module_name} (")
        
        # Ports
        port_lines = []
        for port_name, port_data in self.ports.items():
            direction = port_data['direction']
            bits = port_data.get('bits', [])
            width = len(bits)
            if width > 1:
                port_lines.append(f"    {direction} [{width-1}:0] {port_name}")
            else:
                port_lines.append(f"    {direction} {port_name}")
        
        lines.append(",\n".join(port_lines))
        lines.append(");\n")
        
        # Wires
        # Re-build bit -> name mapping
        net_bit_to_name = {}
        for name, data in self.netnames.items():
            bits = data['bits']
            if not isinstance(bits, list): bits = [bits]
            for bit in bits:
                if isinstance(bit, int):
                    net_bit_to_name[bit] = name

        # Declare wires for all nets that are not ports
        port_names = set(self.ports.keys())
        
        # We should iterate over all defined nets in 'netnames'
        sorted_nets = sorted(self.netnames.keys())
        for net_name in sorted_nets:
            if net_name not in port_names:
                # Check width
                bits = self.netnames[net_name]['bits']
                if not isinstance(bits, list): bits = [bits]
                width = len(bits)
                if width > 1:
                    lines.append(f"    wire [{width-1}:0] {net_name};")
                else:
                    lines.append(f"    wire {net_name};")
        
        lines.append("")
        
        # Cells
        total_cells = len(self.cells)
        print(f"Generating Verilog for {total_cells} cells...")
        
        for i, (cell_name, cell_data) in enumerate(self.cells.items()):
            if i % 10000 == 0:
                print(f"Writing cell {i}/{total_cells}...", flush=True)
                
            cell_type = cell_data['type']
            connections = cell_data['connections']
            
            conn_strs = []
            for port, bits in connections.items():
                if not isinstance(bits, list): bits = [bits]
                
                net_names_for_port = []
                for bit in bits:
                    if isinstance(bit, int):
                        net_name = net_bit_to_name.get(bit, f"net_{bit}")
                        net_names_for_port.append(net_name)
                    else:
                        net_names_for_port.append(str(bit))
                
                if len(net_names_for_port) == 1:
                    conn_strs.append(f".{port}({net_names_for_port[0]})")
                else:
                    conn_str = "{" + ", ".join(reversed(net_names_for_port)) + "}" # Verilog concat is {MSB, ..., LSB}
                    conn_strs.append(f".{port}({conn_str})")
            
            lines.append(f"    {cell_type} {cell_name} (")
            lines.append("        " + ", ".join(conn_strs))
            lines.append("    );")
            lines.append("")
            
        lines.append("endmodule")
        return "\n".join(lines)

def run_eco_flow(design_name: str, netlist_path: str, map_file_path: str, fabric_cells_path: str, fabric_path: str, output_dir: str, pins_path: str = None):
    """
    Run ECO flow with CTS and Power-Down ECO.
    
    Args:
        design_name: Name of the design
        netlist_path: Path to the mapped JSON netlist (input)
        map_file_path: Path to the .map file (logical -> physical mapping from placement)
        fabric_cells_path: Path to fabric_cells.yaml
        fabric_path: Path to fabric.yaml
        fabric_path: Path to fabric.yaml
        output_dir: Directory for output files
        pins_path: Path to pins.yaml (optional, for connecting clock pin)
    """
    print(f"Starting ECO Flow for {design_name}...")
    
    # 1. Load Data - Parse ONCE using parser
    print("Loading data...")
    parser = NetlistParser(netlist_path)
    _, _, netlist_graph_df = parser.parse()
    
    # Access the parsed JSON data from parser (no need to parse twice!)
    netlist_data = parser.data
    top_module_name = parser.top_module
    
    # Parse .map file to get logical -> physical mappings
    print(f"Reading .map file: {map_file_path}")
    map_df = parse_map_file(map_file_path)
    print(f"Found {len(map_df)} cell mappings in .map file")
    
    _, fabric_cells_df = parse_fabric_cells_file(fabric_cells_path)
    _, fabric_df = get_fabric_db(fabric_path, fabric_cells_path)
    
    # 2. Merge .map file with fabric_cells_df to get coordinates
    # Create physical_name -> (x, y) lookup from fabric_cells_df
    physical_to_coords = {}
    for _, row in fabric_cells_df.iterrows():
        if 'cell_name' in row and 'cell_x' in row and 'cell_y' in row:
            physical_name = str(row['cell_name'])
            x = float(row['cell_x'])
            y = float(row['cell_y'])
            physical_to_coords[physical_name] = (x, y)
    
    # Merge map_df with coordinates
    mapped_placement = map_df.copy()
    mapped_placement['x_um'] = mapped_placement['physical_cell_name'].map(
        lambda p: physical_to_coords.get(p, (0, 0))[0]
    )
    mapped_placement['y_um'] = mapped_placement['physical_cell_name'].map(
        lambda p: physical_to_coords.get(p, (0, 0))[1]
    )
    
    # Get cell types from physical names
    template_to_type = dict(zip(fabric_df['cell_name'], fabric_df['cell_type']))
    
    def get_cell_type(physical_name: str) -> str:
        """Extract cell type from physical name."""
        if '__' in physical_name:
            template = physical_name.split('__', 1)[1]
            return template_to_type.get(template, 'UNKNOWN')
        return 'UNKNOWN'
    
    mapped_placement['cell_type'] = mapped_placement['physical_cell_name'].apply(get_cell_type)
    
    print("Unique cell types in placement:")
    print(mapped_placement['cell_type'].unique())
    
    # 3. Identify Resources
    print("Identifying resources...")
    # All physical cells in fabric
    all_physical_cells = set(fabric_cells_df['cell_name'])
    print(f"Total cells in fabric: {len(all_physical_cells)}")
    
    # Used physical cells from .map file
    used_physical_cells = set(mapped_placement['physical_cell_name'])
    print(f"Used cells from .map file: {len(used_physical_cells)}")
    
    # Check for cells in .map that don't exist in fabric (shouldn't happen)
    invalid_cells = used_physical_cells - all_physical_cells
    if invalid_cells:
        print(f"WARNING: {len(invalid_cells)} cells in .map file not found in fabric!")
        print(f"  Sample invalid cells: {list(invalid_cells)[:5]}")
    
    # Unused cells
    unused_cells = all_physical_cells - used_physical_cells
    print(f"Unused cells: {len(unused_cells)}")
    print(f"  (Used: {len(used_physical_cells)}, Unused: {len(unused_cells)}, Total: {len(used_physical_cells) + len(unused_cells)})")
    
    # Filter unused by type
    physical_to_type = {}
    template_to_type = dict(zip(fabric_df['cell_name'], fabric_df['cell_type']))
    
    unused_buffers = []
    unused_ties = []
    unused_logic = []
    cells_without_template = []
    
    for phys_name in unused_cells:
        if '__' in phys_name:
            template = phys_name.split('__', 1)[1]
            ctype = template_to_type.get(template, 'UNKNOWN')
            physical_to_type[phys_name] = ctype
            
            if ctype == 'UNKNOWN':
                cells_without_template.append(phys_name)
            elif 'buf' in ctype.lower() or 'inv' in ctype.lower():
                unused_buffers.append(phys_name)
            elif 'conb' in ctype.lower():
                unused_ties.append(phys_name)
            else:
                unused_logic.append(phys_name)
        else:
            cells_without_template.append(phys_name)
    
    print(f"Found {len(unused_buffers)} unused buffers, {len(unused_ties)} unused ties, {len(unused_logic)} unused logic cells.")
    if cells_without_template:
        print(f"WARNING: {len(cells_without_template)} unused cells could not be categorized (no template or unknown type)")
        if len(cells_without_template) <= 10:
            print(f"  Uncategorized cells: {cells_without_template}")
        else:
            print(f"  Sample uncategorized cells: {cells_without_template[:10]}")
    
    # Verify counts
    categorized = len(unused_buffers) + len(unused_ties) + len(unused_logic)
    print(f"Unused cells breakdown: buffers={len(unused_buffers)}, ties={len(unused_ties)}, logic={len(unused_logic)}, uncategorized={len(cells_without_template)}")
    print(f"Total categorized unused: {categorized}, Total unused: {len(unused_cells)}, Match: {categorized + len(cells_without_template) == len(unused_cells)}")
    
    # 4. CTS Implementation
    print("Running CTS...")
    # Find Sinks (DFFs)
    # sky130 DFFs usually have 'df' in the name (e.g. dfbbp, dfrtp)
    dff_mask = mapped_placement['cell_type'].str.contains('df', case=False, na=False)
    print(f"Rows matching 'df': {dff_mask.sum()}")
    
    sinks_df = mapped_placement[dff_mask]
    sinks = []
    for _, row in sinks_df.iterrows():
        sinks.append(TreeNode(x=row['x_um'], y=row['y_um'], cell_name=row['cell_name'], is_sink=True))
    
    print(f"Found {len(sinks)} clock sinks.")
    
    # Build Tree
    # Simple recursive geometric clustering
    # We need to manage unused buffers as a resource pool with location
    print("Building buffer pool...")
    # Optimize: Filter fabric_cells_df directly instead of looping
    # unused_buffers is a list of strings
    buffer_pool_df = fabric_cells_df[fabric_cells_df['cell_name'].isin(unused_buffers)][['cell_name', 'cell_x', 'cell_y']].copy()
    buffer_pool_df.rename(columns={'cell_name': 'name', 'cell_x': 'x', 'cell_y': 'y'}, inplace=True)
    
    print(f"Buffer pool size: {len(buffer_pool_df)}")
    
    used_buffers = set()
    
    def get_nearest_buffer(target_x, target_y):
        if buffer_pool_df.empty:
            return None
        
        # Filter out used
        # Optimization: maintain a mask or just drop used rows?
        # Since we only use ~150 buffers out of 22k, filtering is okay.
        # But 'isin' check can be slow if used_buffers is large.
        # However, used_buffers is small here.
        
        available = buffer_pool_df[~buffer_pool_df['name'].isin(used_buffers)]
        if available.empty:
            return None
            
        # Simple distance
        # vectorized calculation
        dist = (available['x'] - target_x).abs() + (available['y'] - target_y).abs()
        nearest_idx = dist.idxmin()
        nearest = available.loc[nearest_idx]
        used_buffers.add(nearest['name'])
        return nearest
    
    new_buffers = {} # logical_name -> physical_name
    new_nets = {} # net_name -> bits
    
    # Get module from parsed data (top_module_name already found by parser)
    module = netlist_data["modules"][top_module_name]
    
    # Count original cells in netlist BEFORE any modifications
    original_netlist_cells = len(module['cells'])
    print(f"\nOriginal netlist cells (before ECO): {original_netlist_cells}")
    print(f"Cells in .map file: {len(map_df)}")
    if original_netlist_cells != len(map_df):
        print(f"WARNING: Netlist has {original_netlist_cells} cells but .map has {len(map_df)} entries!")
    
    # Identify Clock Net using netlist_graph_df
    # Find net connected to 'CLK' port of DFFs
    dff_clk_connections = netlist_graph_df[
        (netlist_graph_df['cell_type'].str.contains('df', case=False, na=False)) &
        (netlist_graph_df['port'] == 'CLK')
    ]
    
    if len(dff_clk_connections) == 0:
        print("Warning: Could not identify clock net. Skipping CTS.")
        clock_net_bit = None
    else:
        # Get the clock net bit (should be the same for all DFFs)
        clock_net_bit = dff_clk_connections['net_bit'].iloc[0]
        clock_net_name = dff_clk_connections['net_name'].iloc[0]
        print(f"Identified clock net: {clock_net_name} (bit {clock_net_bit})")
        print(f"  Found {len(dff_clk_connections)} DFF CLK connections")
    
    if clock_net_bit is not None:
        
        # H-Tree Builder with Quadrant Partitioning
        def build_htree(nodes, level=0, parent_x=None, parent_y=None):
            """
            Build an H-tree structure for clock distribution.
            H-tree provides symmetric, balanced clock distribution with minimal skew.
            
            Strategy:
            1. Find geometric center of all nodes
            2. Partition nodes into 4 quadrants around the center
            3. Place buffer at the center
            4. Recursively build subtrees for each quadrant
            """
            # Base case: if few nodes (≤4), create direct connections
            if len(nodes) <= 4:
                # For small groups, just return them as children without further subdivision
                if len(nodes) == 1:
                    return nodes[0]
                    
                # For 2-4 nodes, create a buffer and connect them directly
                if nodes:
                    xs = [n.x for n in nodes]
                    ys = [n.y for n in nodes]
                    center_x = sum(xs) / len(xs)
                    center_y = sum(ys) / len(ys)
                    
                    buf_info = get_nearest_buffer(center_x, center_y)
                    if buf_info is None:
                        print("Warning: Run out of buffers!")
                        return nodes[0]
                    
                    buf_node = TreeNode(
                        x=buf_info['x'], y=buf_info['y'],
                        cell_name=f"cts_htree_{level}_{int(center_x)}_{int(center_y)}",
                        is_sink=False, 
                        physical_name=buf_info['name'],
                        level=level
                    )
                    buf_node.children = nodes
                    return buf_node
            
            # Find bounding box and center
            xs = [n.x for n in nodes]
            ys = [n.y for n in nodes]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            
            # Geometric center (H-tree branching point)
            center_x = (min_x + max_x) / 2
            center_y = (min_y + max_y) / 2
            
            # Partition nodes into 4 quadrants (H-tree characteristic)
            quadrants = {
                'NE': [],  # North-East: x > center, y > center
                'NW': [],  # North-West: x <= center, y > center
                'SE': [],  # South-East: x > center, y <= center
                'SW': []   # South-West: x <= center, y <= center
            }
            
            for node in nodes:
                if node.x > center_x:
                    if node.y > center_y:
                        quadrants['NE'].append(node)
                    else:
                        quadrants['SE'].append(node)
                else:
                    if node.y > center_y:
                        quadrants['NW'].append(node)
                    else:
                        quadrants['SW'].append(node)
            
            # Build children for non-empty quadrants
            children = []
            for quad_name, quad_nodes in quadrants.items():
                if quad_nodes:
                    child_tree = build_htree(quad_nodes, level+1, center_x, center_y)
                    children.append(child_tree)
            
            # Create buffer at the center of this H-tree level
            buf_info = get_nearest_buffer(center_x, center_y)
            if buf_info is None:
                print(f"Warning: Run out of buffers at level {level}!")
                # Fallback: return first child if available
                if children:
                    return children[0]
                return nodes[0] if nodes else None
            
            buf_node = TreeNode(
                x=buf_info['x'], y=buf_info['y'],
                cell_name=f"cts_htree_{level}_{int(center_x)}_{int(center_y)}",
                is_sink=False,
                physical_name=buf_info['name'],
                level=level
            )
            buf_node.children = children
            
            return buf_node

        # Build the H-tree
        root_node = build_htree(sinks)
        
        # If root_node is a single sink (len(sinks)=1), we might want to add a buffer anyway?
        # Or just let it be.
        # If len(sinks) > 1, root_node will be a buffer.
        
        # Traverse and Update Netlist
        
        # Traverse and Update Netlist
        # Find max net bit to allocate new bits
        max_net_bit = 0
        for net_data in module['netnames'].values():
            for bit in net_data['bits']:
                if isinstance(bit, int):
                    max_net_bit = max(max_net_bit, bit)
        
        next_net_bit = max_net_bit + 1
        
        def traverse_update(node, input_net_bit):
            nonlocal next_net_bit
            
            if node.is_sink:
                # Disconnect DFF from original clock, connect to input_net_bit
                cell = module['cells'][node.cell_name]
                cell['connections']['CLK'] = [input_net_bit]
                return
            
            # It's a buffer
            output_net_bit = next_net_bit
            next_net_bit += 1
            
            # Add netname
            net_name = f"cts_net_{output_net_bit}"
            module['netnames'][net_name] = {'bits': [output_net_bit], 'attributes': {}}
            
            # Add cell
            module['cells'][node.cell_name] = {
                'type': 'sky130_fd_sc_hd__buf_1', # Assuming type
                'connections': {
                    'A': [input_net_bit],
                    'X': [output_net_bit]
                },
                'attributes': {} # Add physical mapping?
            }
            
            # Recurse
            for child in node.children:
                traverse_update(child, output_net_bit)

        if isinstance(root_node, TreeNode):
            # Initialize CTS Data for Visualization EARLY
            cts_data = {
                'sinks': [{'name': s.cell_name, 'x': float(s.x), 'y': float(s.y)} for s in sinks],
                'buffers': [],
                'connections': []
            }
            
            # NEW: Connect Clock Pin to Root Node
            if pins_path:
                print(f"Loading pins from {pins_path} to find clock pin...")
                pins_df, _ = load_pins_df(pins_path)
                # Look for clock pin (usually 'clk' or similar)
                # We can check the netlist for the top-level port name connected to the clock net
                # But for now, let's assume 'clk' or try to find it.
                
                # Find the port name connected to clock_net_name in the top module
                clock_port_name = None
                for port_name, port_data in module['ports'].items():
                    # Check if this port is connected to the clock net
                    # Port bits are usually mapped to net bits
                    # This is a bit complex to reverse without a full netlist graph, 
                    # but usually the port name IS the net name for top-level ports.
                    if port_name == clock_net_name:
                        clock_port_name = port_name
                        break
                
                if not clock_port_name:
                    # Fallback: look for 'clk' in pins
                    clock_port_name = 'clk'
                
                print(f"Looking for pin location for port: {clock_port_name}")
                clk_pin_row = pins_df[pins_df['name'] == clock_port_name]
                
                if not clk_pin_row.empty:
                    pin_x = float(clk_pin_row.iloc[0]['x_um'])
                    pin_y = float(clk_pin_row.iloc[0]['y_um'])
                    print(f"Found clock pin at ({pin_x}, {pin_y})")
                    
                    # Create a node for the pin
                    pin_node = TreeNode(
                        x=pin_x, y=pin_y,
                        cell_name=f"PIN_{clock_port_name}",
                        is_sink=False, # It's a source, but for visualization we treat it as a node
                        physical_name="PIN",
                        level=-1
                    )
                    
                    # Connect pin to root
                    # Add to visualization data
                    cts_data['buffers'].append({
                        'name': pin_node.cell_name,
                        'physical_name': "PIN",
                        'x': pin_x,
                        'y': pin_y,
                        'level': -1
                    })
                    
                    cts_data['connections'].append({
                        'from': {'x': pin_x, 'y': pin_y},
                        'to': {'x': float(root_node.x), 'y': float(root_node.y)}
                    })
                    print("Added clock pin connection to visualization data.")
                    
                else:
                    print(f"Warning: Clock pin '{clock_port_name}' not found in pins file.")

            traverse_update(root_node, clock_net_bit)
            print("CTS Netlist update complete.")
            
            # Save CTS Data for Visualization
            print("Saving CTS data for visualization...")
            
            def collect_cts_data(node):
                if not node.is_sink:
                    cts_data['buffers'].append({
                        'name': node.cell_name,
                        'physical_name': node.physical_name,
                        'x': float(node.x),
                        'y': float(node.y),
                        'level': node.level
                    })
                    for child in node.children:
                        cts_data['connections'].append({
                            'from': {'x': float(node.x), 'y': float(node.y)},
                            'to': {'x': float(child.x), 'y': float(child.y)}
                        })
                        collect_cts_data(child)
            
            if isinstance(root_node, TreeNode):
                collect_cts_data(root_node)
                
            cts_json_path = Path(output_dir) / f"{design_name}_cts.json"
            with open(cts_json_path, 'w') as f:
                json.dump(cts_data, f, indent=2)
            print(f"CTS data written to {cts_json_path}")
        
    # 6. Power-Down ECO: Tie unused logic cells
    print("Implementing Power-Down ECO...")
    
    # Step 1: Build a cache of cell_type -> port_directions from existing cells
    # This avoids heuristics by using actual netlist data
    cell_type_port_directions = {}
    for cell_name, cell_data in module['cells'].items():
        cell_type = cell_data.get('type', 'UNKNOWN')
        if cell_type not in cell_type_port_directions:
            port_directions = cell_data.get('port_directions', {})
            if port_directions:
                cell_type_port_directions[cell_type] = port_directions.copy()
    
    print(f"Found port_directions for {len(cell_type_port_directions)} cell types")
    
    # Step 2: Distributed tie cell approach
    if not unused_ties:
        print("Warning: No unused tie cells (conb_1) found. Skipping Power-Down ECO.")
    else:
        # Pre-count how many unused logic cells will actually be tied
        # (Many get filtered out: taps, decaps, no inputs, no port info)
        cells_that_will_be_tied = 0
        skipped_taps_decap = 0
        skipped_no_inputs = 0
        skipped_no_port_info = 0
        skipped_unknown = 0
        
        for phys_name in unused_logic:
            # Get cell type from physical name
            if '__' in phys_name:
                template = phys_name.split('__', 1)[1]
                cell_type = template_to_type.get(template, 'UNKNOWN')
            else:
                cell_type = physical_to_type.get(phys_name, 'UNKNOWN')
            
            if cell_type == 'UNKNOWN':
                skipped_unknown += 1
                continue
            
            # Skip cells that don't have logic inputs (taps, decaps, conb, etc.)
            if 'tap' in cell_type.lower() or 'decap' in cell_type.lower() or 'conb' in cell_type.lower():
                skipped_taps_decap += 1
                continue
            
            # Get port_directions for this cell type
            port_directions = cell_type_port_directions.get(cell_type)
            if not port_directions:
                # Try to find a similar cell type
                for known_type, known_dirs in cell_type_port_directions.items():
                    base_known = known_type.split('__')[-1].split('_')[0] if '__' in known_type else known_type.split('_')[0]
                    base_current = cell_type.split('__')[-1].split('_')[0] if '__' in cell_type else cell_type.split('_')[0]
                    if base_known == base_current:
                        port_directions = known_dirs
                        break
                
                if not port_directions:
                    skipped_no_port_info += 1
                    continue
            
            # Extract input ports only
            input_ports = [port for port, direction in port_directions.items() 
                          if direction.lower() == 'input']
            
            if not input_ports:
                skipped_no_inputs += 1
                continue
            
            cells_that_will_be_tied += 1
        
        # Calculate how many tie cells we need based on ACTUAL cells that will be tied
        # Fanout limit: 1000 cells per tie cell (reasonable for power/ground nets)
        MAX_FANOUT_PER_TIE = 1000
        num_tie_cells_needed = max(1, math.ceil(cells_that_will_be_tied / MAX_FANOUT_PER_TIE))
        num_tie_cells_needed = min(num_tie_cells_needed, len(unused_ties))  # Don't exceed available
        
        print(f"Power-Down ECO: {len(unused_logic)} unused logic cells identified")
        print(f"  - Will tie: {cells_that_will_be_tied} cells")
        print(f"  - Skipped: {skipped_taps_decap} (taps/decap/conb), {skipped_no_inputs} (no inputs), {skipped_no_port_info} (no port info), {skipped_unknown} (unknown type)")
        print(f"  - Need {num_tie_cells_needed} tie cells (fanout limit: {MAX_FANOUT_PER_TIE} per tie cell)")
        print(f"  - Available: {len(unused_ties)} unused tie cells")
        
        # Select tie cells distributed spatially across the fabric
        # Get coordinates for all unused tie cells
        tie_cell_coords = []
        for tie_phys_name in unused_ties:
            coords = physical_to_coords.get(tie_phys_name)
            if coords:
                tie_cell_coords.append((tie_phys_name, coords[0], coords[1]))
        
        if not tie_cell_coords:
            print("Warning: Could not get coordinates for tie cells. Using first available.")
            selected_tie_cells = unused_ties[:num_tie_cells_needed]
        else:
            # Sort by coordinates and select evenly distributed ones
            # Simple approach: divide into grid regions and pick one from each
            tie_df = pd.DataFrame(tie_cell_coords, columns=['physical_name', 'x', 'y'])
            
            # Use k-means-like approach: divide into num_tie_cells_needed regions
            if num_tie_cells_needed == 1:
                # Just pick the one closest to center
                center_x = tie_df['x'].mean()
                center_y = tie_df['y'].mean()
                tie_df['dist_to_center'] = ((tie_df['x'] - center_x)**2 + (tie_df['y'] - center_y)**2)**0.5
                selected_tie_cells = [tie_df.loc[tie_df['dist_to_center'].idxmin(), 'physical_name']]
            else:
                # Divide into grid and pick closest to center of each grid cell
                x_min, x_max = tie_df['x'].min(), tie_df['x'].max()
                y_min, y_max = tie_df['y'].min(), tie_df['y'].max()
                
                # Calculate grid dimensions (roughly square)
                grid_size = int(math.ceil(math.sqrt(num_tie_cells_needed)))
                x_step = (x_max - x_min) / grid_size if x_max > x_min else 1
                y_step = (y_max - y_min) / grid_size if y_max > y_min else 1
                
                selected_tie_cells = []
                for i in range(grid_size):
                    for j in range(grid_size):
                        if len(selected_tie_cells) >= num_tie_cells_needed:
                            break
                        # Center of this grid cell
                        grid_center_x = x_min + (i + 0.5) * x_step
                        grid_center_y = y_min + (j + 0.5) * y_step
                        
                        # Find tie cell closest to this grid center
                        tie_df['dist_to_grid_center'] = (
                            (tie_df['x'] - grid_center_x)**2 + 
                            (tie_df['y'] - grid_center_y)**2
                        )**0.5
                        
                        # Exclude already selected cells
                        available = tie_df[~tie_df['physical_name'].isin(selected_tie_cells)]
                        if len(available) > 0:
                            closest = available.loc[available['dist_to_grid_center'].idxmin(), 'physical_name']
                            selected_tie_cells.append(closest)
                    if len(selected_tie_cells) >= num_tie_cells_needed:
                        break
                
                # If we didn't get enough, fill with remaining
                remaining = [t for t in unused_ties if t not in selected_tie_cells]
                while len(selected_tie_cells) < num_tie_cells_needed and remaining:
                    selected_tie_cells.append(remaining.pop(0))
        
        print(f"Selected {len(selected_tie_cells)} tie cells for distribution")
        
        # Find max net bit (in case CTS added nets)
        max_net_bit = 0
        for net_data in module['netnames'].values():
            bits = net_data.get('bits', [])
            if not isinstance(bits, list):
                bits = [bits]
            for bit in bits:
                if isinstance(bit, int):
                    max_net_bit = max(max_net_bit, bit)
        
        # Create local tie nets for each tie cell
        # We will store the net bits for each tie cell index
        tie_nets_map = {} # idx -> {'low': bit, 'high': bit}
        
        # Step 3 & 4: Add tie nets and cells to netlist
        # Get port_directions for conb_1 if available, otherwise use known structure
        conb_port_directions = cell_type_port_directions.get('sky130_fd_sc_hd__conb_1', {
            'LO': 'output',
            'HI': 'output'
        })
        
        current_net_bit = max_net_bit + 1
        
        for idx, tie_cell_physical_name in enumerate(selected_tie_cells):
            # Create local nets for this tie cell
            tie_low_net_bit = current_net_bit
            tie_high_net_bit = current_net_bit + 1
            current_net_bit += 2
            
            tie_low_net_name = f"tie_low_net_{idx}"
            tie_high_net_name = f"tie_high_net_{idx}"
            
            module['netnames'][tie_low_net_name] = {'bits': [tie_low_net_bit], 'attributes': {}}
            module['netnames'][tie_high_net_name] = {'bits': [tie_high_net_bit], 'attributes': {}}
            
            tie_nets_map[idx] = {
                'low': tie_low_net_bit,
                'high': tie_high_net_bit,
                'low_name': tie_low_net_name,
                'high_name': tie_high_net_name
            }
            
            tie_cell_logical_name = f"tie_cell_{idx}"
            
            module['cells'][tie_cell_logical_name] = {
                'type': 'sky130_fd_sc_hd__conb_1',
                'connections': {
                    'LO': [tie_low_net_bit],   # LO outputs 0 (tie-low)
                    'HI': [tie_high_net_bit]   # HI outputs 1 (tie-high)
                },
                'port_directions': conb_port_directions,
                'attributes': {
                    'physical_name': tie_cell_physical_name
                }
            }
        
        print(f"Added {len(selected_tie_cells)} tie cells with local nets")
        
        # Step 5: Helper function to determine if a port should be tied high or low
        def should_tie_high(port_name: str) -> bool:
            """
            Active-low signals (ending in _B, _N, _n) should be tied HIGH.
            Regular inputs should be tied LOW.
            """
            port_upper = port_name.upper()
            # Active-low signals (ending in _B or _N)
            if port_upper.endswith('_B') or port_upper.endswith('_N'):
                return True
            # Clock signals should typically be tied low (though unused DFFs shouldn't exist)
            if 'CLK' in port_upper:
                return False
            # Default: tie low
            return False
        
        # Step 6: Build spatial assignment map (which tie cell is nearest to each unused cell)
        # This is for statistics - all unused cells connect to the same tie nets
        tie_cell_coords_map = {}
        for idx, tie_phys_name in enumerate(selected_tie_cells):
            coords = physical_to_coords.get(tie_phys_name)
            if coords:
                tie_cell_coords_map[idx] = (coords[0], coords[1])
        
        # Track assignment statistics
        tie_cell_assignments = {idx: 0 for idx in range(len(selected_tie_cells))}
        
        # Step 7: Add unused logic cells to netlist with tied inputs
        unused_logic_added = 0
        cells_without_port_info = set()
        
        # Load leakage optimal vectors
        optimal_vectors = {}
        json_path = Path("inputs") / "leakage_optimal_vectors.json"
        if json_path.exists():
            print(f"Loading leakage optimization vectors from {json_path}")
            with open(json_path, 'r') as f:
                optimal_vectors = json.load(f)
        else:
            print("Warning: leakage_optimal_vectors.json not found. Using default tying logic.")

        for phys_name in unused_logic:
            # Get cell type from physical name
            if '__' in phys_name:
                template = phys_name.split('__', 1)[1]
                cell_type = template_to_type.get(template, 'UNKNOWN')
            else:
                cell_type = physical_to_type.get(phys_name, 'UNKNOWN')
            
            if cell_type == 'UNKNOWN':
                print(f"Warning: Could not determine cell type for {phys_name}, skipping")
                continue
            
            # Skip cells that don't have logic inputs (taps, decaps, conb, etc.)
            if 'tap' in cell_type.lower() or 'decap' in cell_type.lower() or 'conb' in cell_type.lower():
                continue
            
            # Get port_directions for this cell type
            port_directions = cell_type_port_directions.get(cell_type)
            if not port_directions:
                # Try to find a similar cell type (e.g., nand2_1 vs nand2_2)
                for known_type, known_dirs in cell_type_port_directions.items():
                    # Extract base name (e.g., "nand2" from "sky130_fd_sc_hd__nand2_1")
                    base_known = known_type.split('__')[-1].split('_')[0] if '__' in known_type else known_type.split('_')[0]
                    base_current = cell_type.split('__')[-1].split('_')[0] if '__' in cell_type else cell_type.split('_')[0]
                    if base_known == base_current:
                        port_directions = known_dirs
                        break
                
                if not port_directions:
                    cells_without_port_info.add(cell_type)
                    continue
            
            # Extract input ports only
            input_ports = [port for port, direction in port_directions.items() 
                          if direction.lower() == 'input']
            
            if not input_ports:
                continue  # Skip cells with no inputs
            
            # Create logical name for this unused cell
            logical_name = f"unused_{phys_name}"
            
            # Find nearest tie cell for statistics (all connect to same nets anyway)
            if tie_cell_coords_map:
                unused_coords = physical_to_coords.get(phys_name)
                if unused_coords:
                    min_dist = float('inf')
                    nearest_tie_idx = 0
                    for tie_idx, tie_coords in tie_cell_coords_map.items():
                        dist = ((unused_coords[0] - tie_coords[0])**2 + 
                               (unused_coords[1] - tie_coords[1])**2)**0.5
                        if dist < min_dist:
                            min_dist = dist
                            nearest_tie_idx = tie_idx
                    tie_cell_assignments[nearest_tie_idx] += 1
            

            # Build connections: tie inputs appropriately
            # Use the local tie nets from the nearest tie cell
            tie_nets = tie_nets_map.get(nearest_tie_idx)
            if not tie_nets:
                # Fallback if something went wrong (shouldn't happen)
                print(f"Error: No tie nets found for index {nearest_tie_idx}")
                continue
                
            connections = {}
            
            # Check for optimal vector for this cell type
            # Strip template if present to match parser output names if needed?
            # Parser seems to use full names like "sky130_fd_sc_hd__nand2_1"
            # Our cell_type variable should match that.
            
            cell_optimal = optimal_vectors.get(cell_type, {})
            
            for port in input_ports:
                # Determine tie value (0 or 1)
                tie_val = 0 # Default low
                
                if port in cell_optimal:
                    tie_val = cell_optimal[port]
                else:
                    # Fallback to heuristic
                    tie_val = 1 if should_tie_high(port) else 0
                
                if tie_val == 1:
                    connections[port] = [tie_nets['high']]
                else:
                    connections[port] = [tie_nets['low']]
            
            # Add cell to netlist
            module['cells'][logical_name] = {
                'type': cell_type,
                'connections': connections,
                'port_directions': port_directions.copy(),
                'attributes': {
                    'physical_name': phys_name,
                    'unused': True
                }
            }
            unused_logic_added += 1
        
        if cells_without_port_info:
            print(f"Warning: Could not find port_directions for {len(cells_without_port_info)} cell types: {sorted(cells_without_port_info)}")
        
        print(f"Power-Down ECO complete: Tied {unused_logic_added} unused logic cells")
        print(f"  - Tie-low net: {tie_low_net_name} (bit {tie_low_net_bit})")
        print(f"  - Tie-high net: {tie_high_net_name} (bit {tie_high_net_bit})")
        print(f"  - Using {len(selected_tie_cells)} distributed tie cells")
        
        # Print assignment statistics
        if tie_cell_assignments:
            print(f"  - Tie cell assignment statistics:")
            total_assigned = sum(tie_cell_assignments.values())
            actual_avg = total_assigned / len(selected_tie_cells) if len(selected_tie_cells) > 0 else 0
            for tie_idx in sorted(tie_cell_assignments.keys()):
                count = tie_cell_assignments[tie_idx]
                print(f"    Tie cell {tie_idx}: {count} cells assigned")
            max_fanout = max(tie_cell_assignments.values()) if tie_cell_assignments else 0
            min_fanout = min(tie_cell_assignments.values()) if tie_cell_assignments else 0
            print(f"  - Fanout: {min_fanout} - {max_fanout} cells per tie cell (avg: {actual_avg:.0f})")
        
        # Count cells added during ECO
        cts_buffers_added = len([c for c in module['cells'].keys() if c.startswith('cts_htree_')])
        tie_cells_added = len([c for c in module['cells'].keys() if c.startswith('tie_cell_')])
        unused_cells_added = len([c for c in module['cells'].keys() if c.startswith('unused_')])
        original_cells = len(module['cells']) - cts_buffers_added - tie_cells_added - unused_cells_added
        
        print(f"\nCell count breakdown:")
        print(f"  Original cells (from netlist, before ECO): {original_netlist_cells}")
        print(f"  Original cells (calculated from final - added): {original_cells}")
        print(f"  Cells in .map file: {len(used_physical_cells)}")
        print(f"  CTS buffers added: {cts_buffers_added}")
        print(f"  Tie cells added: {tie_cells_added}")
        print(f"  Unused logic cells added: {unused_cells_added}")
        print(f"  Total cells in netlist after ECO: {len(module['cells'])}")
        
        # Expected: original + CTS + tie + unused_logic
        expected_total = original_netlist_cells + cts_buffers_added + tie_cells_added + unused_logic_added
        print(f"  Expected total: {original_netlist_cells} (original) + {cts_buffers_added} (CTS) + {tie_cells_added} (tie) + {unused_logic_added} (unused logic) = {expected_total}")
        print(f"  Actual vs Expected: {len(module['cells'])} vs {expected_total}, Difference: {len(module['cells']) - expected_total}")
        
        # Also check fabric totals
        print(f"\nFabric totals:")
        print(f"  Total fabric cells: {len(all_physical_cells)}")
        print(f"  Used cells (from .map): {len(used_physical_cells)}")
        print(f"  Unused cells: {len(unused_cells)}")
        print(f"  Unused logic added to netlist: {unused_logic_added}")
        print(f"  Unused NOT added (taps/decap/fill/no-inputs): {len(unused_cells) - unused_logic_added}")
        print(f"  Fabric total should equal: {len(used_physical_cells)} + {len(unused_cells)} = {len(used_physical_cells) + len(unused_cells)}")
        
    # 5. Generate Verilog
    print("Generating Verilog...")
    writer = VerilogWriter(top_module_name, module['ports'], module['cells'], module['netnames'])
    verilog_code = writer.generate()
    
    output_path = Path(output_dir) / f"{design_name}_final.v"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(verilog_code)
        
    print(f"Verilog written to {output_path}")
