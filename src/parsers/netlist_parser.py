"""
Parser JSON netlist files.

This module parses [design_name]_mapped.json files to create:
1. logical_db: A pandas DataFrame of logical cells grouped by type
2. ports_df: A pandas DataFrame of module ports with netnames
3. netlist_graph_db: A pandas DataFrame of cell connections with netnames
"""

import json
from typing import Dict, Tuple
import pandas as pd


class NetlistParser:
    """Parser for Yosys JSON netlist files using pandas DataFrames."""
    
    def __init__(self, json_file_path: str):
        """
        Initialize the parser with a JSON file path.
        
        Args:
            json_file_path: Path to the [design_name]_mapped.json file
        """
        self.json_file_path = json_file_path
        self.data = None
        self.top_module = None
        self.logical_db_df = None  # DataFrame: [cell_type, cell_name]
        self.ports_df = None  # DataFrame: [port_name, direction, net_bit, net_name]
        self.netlist_graph_db = None  # DataFrame: [cell_name, cell_type, port, net_bit, net_name, direction]
        self.net_bit_to_name = {}  # Mapping: net_bit -> net_name
        
    def parse(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Parse the JSON file and create logical_db, ports_df, and netlist_graph_db.
        
        Returns:
            Tuple of (logical_db, ports_df, netlist_graph_db) where:
            - logical_db: DataFrame with columns [cell_type, cell_name]
            - ports_df: DataFrame with columns [port_name, direction, net_bit, net_name]
            - netlist_graph_db: DataFrame with columns [cell_name, cell_type, port, net_bit, net_name, direction]
        """
        # Load JSON file
        with open(self.json_file_path, 'r') as f:
            self.data = json.load(f)
        
        # Find the top module (usually "sasic_top" or the module with "top" attribute)
        self.top_module = self._find_top_module()
        
        if not self.top_module:
            raise ValueError("No top module found in the JSON file")
        
        # Get the top module data
        top_module_data = self.data['modules'][self.top_module]
        
        # Parse netnames to create net_bit -> net_name mapping
        self._parse_netnames(top_module_data)
        
        # Parse cells to create logical_db DataFrame
        self._parse_cells(top_module_data)
        
        # Parse ports to create ports_df DataFrame
        self._parse_ports(top_module_data)
        
        # Build netlist_graph_db DataFrame with netnames
        self._build_netlist_graph_db(top_module_data)
        
        return self.logical_db_df, self.ports_df, self.netlist_graph_db
    
    def _find_top_module(self) -> str:
        """Find the top module in the JSON file."""
        # First, try to find a module with "top" attribute set to 1
        for module_name, module_data in self.data.get('modules', {}).items():
            attrs = module_data.get('attributes', {})
            if attrs.get('top') == '00000000000000000000000000000001':
                return module_name
        
        # If no top attribute, look for common top module names
        for name in ['sasic_top', 'top', 'TOP']:
            if name in self.data.get('modules', {}):
                return name
        
        # If still not found, return the first module
        modules = list(self.data.get('modules', {}).keys())
        if modules:
            return modules[0]
        
        return None
    
    def _parse_netnames(self, module_data: Dict):
        """
        Parse netnames from the module to create net_bit -> net_name mapping.
        
        Args:
            module_data: The data dictionary for the top module
        """
        netnames = module_data.get('netnames', {})
        self.net_bit_to_name = {}
        
        for net_name, net_data in netnames.items():
            bits = net_data.get('bits', [])
            if not isinstance(bits, list):
                bits = [bits]
            
            # Map each net bit to the net name
            for net_bit in bits:
                if net_bit is not None:
                    # If multiple netnames map to same bit, keep the first one
                    # (or you could concatenate them, but typically one netname per bit)
                    if net_bit not in self.net_bit_to_name:
                        self.net_bit_to_name[net_bit] = net_name
    
    def _parse_cells(self, module_data: Dict):
        """
        Parse cells from the module and create logical_db DataFrame.
        
        Args:
            module_data: The data dictionary for the top module
        """
        cells = module_data.get('cells', {})
        
        # Build list of records for DataFrame
        logical_db_records = []
        
        for cell_name, cell_data in cells.items():
            cell_type = cell_data.get('type', 'UNKNOWN')
            
            # Add record to logical_db
            logical_db_records.append({
                'cell_type': cell_type,
                'cell_name': cell_name
            })
        
        # Create DataFrame from records
        self.logical_db_df = pd.DataFrame(logical_db_records)
    
    def _parse_ports(self, module_data: Dict):
        """
        Parse ports from the module and create ports_df DataFrame with netnames.
        
        Args:
            module_data: The data dictionary for the top module
        """
        ports = module_data.get('ports', {})
        port_records = []
        
        for port_name, port_data in ports.items():
            direction = port_data.get('direction', 'unknown')
            bits = port_data.get('bits', [])
            if not isinstance(bits, list):
                bits = [bits]
            
            # For each bit of the port
            for net_bit in bits:
                if net_bit is not None:
                    # Get net name from mapping, or use None if not found
                    net_name = self.net_bit_to_name.get(net_bit, None)
                    
                    port_records.append({
                        'port_name': port_name,
                        'direction': direction,
                        'net_bit': net_bit,
                        'net_name': net_name
                    })
        
        # Create DataFrame from records
        self.ports_df = pd.DataFrame(port_records)
    
    def _build_netlist_graph_db(self, module_data: Dict):
        """
        Build netlist_graph_db DataFrame with netnames for all cell-port-net connections.
        
        Args:
            module_data: The data dictionary for the top module
        """
        cells = module_data.get('cells', {})
        cell_records = []
        
        for cell_name, cell_data in cells.items():
            cell_type = cell_data.get('type', 'UNKNOWN')
            connections = cell_data.get('connections', {})
            port_directions = cell_data.get('port_directions', {})
            
            # For each port connection
            for port_name, net_bits in connections.items():
                if not isinstance(net_bits, list):
                    net_bits = [net_bits]
                
                direction = port_directions.get(port_name, 'unknown')
                
                # For each net bit connected to this port
                for net_bit in net_bits:
                    if net_bit is not None:  # Skip None/null connections
                        # Get net name from mapping, or use None if not found
                        net_name = self.net_bit_to_name.get(net_bit, None)
                        
                        cell_records.append({
                            'cell_name': cell_name,
                            'cell_type': cell_type,
                            'port': port_name,
                            'net_bit': net_bit,
                            'net_name': net_name,
                            'direction': direction
                        })
        
        # Create DataFrame from records
        self.netlist_graph_db = pd.DataFrame(cell_records)
    
def parse_netlist(json_file_path: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Convenience function to parse a netlist JSON file.
    
    Args:
        json_file_path: Path to the [design_name]_mapped.json file
        
    Returns:
        Tuple of (logical_db, ports_df, netlist_graph_db) where:
        - logical_db: DataFrame with columns [cell_type, cell_name]
        - ports_df: DataFrame with columns [port_name, direction, net_bit, net_name]
        - netlist_graph_db: DataFrame with columns [cell_name, cell_type, port, net_bit, net_name, direction]
    """
    parser = NetlistParser(json_file_path)
    return parser.parse()


def get_logical_db(json_file_path: str) -> pd.DataFrame:
    """
    Convenience function to get only the logical_db DataFrame.
    
    Args:
        json_file_path: Path to the [design_name]_mapped.json file
        
    Returns:
        logical_db: DataFrame with columns [cell_type, cell_name]
    """
    parser = NetlistParser(json_file_path)
    logical_db, _, _ = parser.parse()
    return logical_db


def get_netlist_graph(json_file_path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Convenience function to get only the ports_df and netlist_graph_db DataFrames.
    
    Args:
        json_file_path: Path to the [design_name]_mapped.json file
        
    Returns:
        Tuple of (ports_df, netlist_graph_db) where:
        - ports_df: DataFrame with columns [port_name, direction, net_bit, net_name]
        - netlist_graph_db: DataFrame with columns [cell_name, cell_type, port, net_bit, net_name, direction]
    """
    parser = NetlistParser(json_file_path)
    _, ports_df, netlist_graph_db = parser.parse()
    return ports_df, netlist_graph_db


if __name__ == "__main__":
    # Example usage
    import sys
    from pathlib import Path
    
    if len(sys.argv) < 2:
        print("Usage: python netlist_parser.py <path_to_mapped.json>")
        sys.exit(1)
    
    json_path = sys.argv[1]
    logical_db, ports_df, netlist_graph_db = parse_netlist(json_path)
    
    print("Logical Database (cells grouped by type):")
    print("=" * 60)
    cell_counts = logical_db['cell_type'].value_counts()
    for cell_type, count in cell_counts.items():
        print(f"{cell_type}: {count} instances")
    
    print(f"\nTotal cells: {len(logical_db)}")
    print(f"\nPorts DataFrame:")
    print(f"Total ports: {len(ports_df)}")
    print(f"Unique port names: {ports_df['port_name'].nunique()}")
    print(f"Unique nets: {ports_df['net_bit'].nunique()}")
    
    print(f"\nNetlist Graph Database (connectivity):")
    print(f"Total connections: {len(netlist_graph_db)}")
    print(f"Unique nets: {netlist_graph_db['net_bit'].nunique()}")
    print(f"Unique cells: {netlist_graph_db['cell_name'].nunique()}")
    
    # Show sample of DataFrames
    print("\n" + "=" * 60)
    print("Sample logical_db:")
    print(logical_db.head())
    print(f"Shape: {logical_db.shape}")
    
    print("\n" + "=" * 60)
    print("Sample ports_df:")
    print(ports_df.head())
    print(f"Shape: {ports_df.shape}")
    
    print("\n" + "=" * 60)
    print("Sample netlist_graph_db:")
    print(netlist_graph_db.head())
    print(f"Shape: {netlist_graph_db.shape}")

