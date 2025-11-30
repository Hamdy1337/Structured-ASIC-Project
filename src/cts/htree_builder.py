"""
eco_generator.py: Generates ECO netlist with H-tree Clock Tree Synthesis (CTS).
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
from src.placement.placement_mapper import map_placement_to_physical_cells

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

def run_eco_flow(design_name: str, netlist_path: str, placement_path: str, fabric_cells_path: str, fabric_path: str, output_dir: str):
    print(f"Starting ECO Flow for {design_name}...")
    
    # 1. Load Data
    print("Loading data...")
    with open(netlist_path, 'r') as f:
        netlist_data = json.load(f)
    
    placement_df = pd.read_csv(placement_path)
    _, fabric_cells_df = parse_fabric_cells_file(fabric_cells_path)
    _, fabric_df = get_fabric_db(fabric_path, fabric_cells_path)
    
    # 2. Map Placement to Physical Cells
    print("Mapping placement to physical cells...")
    mapped_placement = map_placement_to_physical_cells(placement_df, fabric_cells_df, fabric_df)
    
    print("Unique cell types in placement:")
    print(mapped_placement['cell_type'].unique())
    
    # 3. Identify Resources
    print("Identifying resources...")
    # All physical cells
    all_physical_cells = set(fabric_cells_df['cell_name'])
    # Used physical cells
    used_physical_cells = set(mapped_placement['physical_cell_name'])
    # Unused cells
    unused_cells = all_physical_cells - used_physical_cells
    
    # Filter unused by type
    physical_to_type = {}
    template_to_type = dict(zip(fabric_df['cell_name'], fabric_df['cell_type']))
    
    unused_buffers = []
    unused_ties = []
    unused_logic = []
    
    for phys_name in unused_cells:
        if '__' in phys_name:
            template = phys_name.split('__', 1)[1]
            ctype = template_to_type.get(template, 'UNKNOWN')
            physical_to_type[phys_name] = ctype
            
            if 'buf' in ctype.lower() or 'inv' in ctype.lower():
                unused_buffers.append(phys_name)
            elif 'conb' in ctype.lower():
                unused_ties.append(phys_name)
            else:
                unused_logic.append(phys_name)
    
    print(f"Found {len(unused_buffers)} unused buffers, {len(unused_ties)} unused ties, {len(unused_logic)} unused logic cells.")
    
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
    
    # Find top module
    top_module_name = "sasic_top"
    if design_name in netlist_data["modules"]:
        top_module_name = design_name
    elif "sasic_top" in netlist_data["modules"]:
        top_module_name = "sasic_top"
    else:
        top_module_name = list(netlist_data["modules"].keys())[0]
        
    module = netlist_data["modules"][top_module_name]
    
    # Identify Clock Net
    # Heuristic: Net connected to 'CLK' port of DFFs
    clock_net_bit = None
    for _, cell in module['cells'].items():
        if 'DFF' in cell['type'] or 'df' in cell['type'].lower():
            if 'CLK' in cell['connections']:
                clock_net_bit = cell['connections']['CLK'][0]
                break
    
    if clock_net_bit is None:
        print("Warning: Could not identify clock net. Skipping CTS.")
    else:
        print(f"Identified clock net bit: {clock_net_bit}")
        
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
            traverse_update(root_node, clock_net_bit)
            print("CTS Netlist update complete.")
            
            # Save CTS Data for Visualization
            print("Saving CTS data for visualization...")
            cts_data = {
                'sinks': [{'name': s.cell_name, 'x': float(s.x), 'y': float(s.y)} for s in sinks],
                'buffers': [],
                'connections': []
            }
            
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
        
        
    # 5. Generate Verilog
    print("Generating Verilog...")
    writer = VerilogWriter(top_module_name, module['ports'], module['cells'], module['netnames'])
    verilog_code = writer.generate()
    
    output_path = Path(output_dir) / f"{design_name}_final.v"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(verilog_code)
        
    print(f"Verilog written to {output_path}")
