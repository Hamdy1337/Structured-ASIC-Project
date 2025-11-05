"""
Parser JSON netlist files.

This module parses [design_name]_mapped.json files to create:
1. logical_db: A pandas DataFrame of logical cells grouped by type
2. netlist_graph: A pandas DataFrame representation of the netlist connectivity
"""

import json
from typing import Dict, List, Set, Tuple, Any
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
        self.logical_db_df = None  # DataFrame: [cell_type, cell_instance] - returned as logical_db
        self.cell_connections_df = None  # DataFrame: [cell_name, cell_type, port, net_bit, direction] - returned as netlist_graph
        self.net_to_cells_df = None  # DataFrame: [net_bit, cell_name] - optional, accessible via parser instance
        
    def parse(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Parse the JSON file and create logical_db and netlist_graph.
        
        Returns:
            Tuple of (logical_db, netlist_graph) where:
            - logical_db: DataFrame with columns [cell_type, cell_instance]
            - netlist_graph: DataFrame with columns [cell_name, cell_type, port, net_bit, direction]
                            representing all cell-port-net connections
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
        
        # Parse cells to create logical_db DataFrame
        self._parse_cells(top_module_data)
        
        # Build netlist graph DataFrames
        self._build_netlist_graph(top_module_data)
        
        # logical_db: DataFrame with [cell_type, cell_instance]
        # netlist_graph: DataFrame with [cell_name, cell_type, port, net_bit, direction]
        return self.logical_db_df, self.cell_connections_df
    
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
                'cell_instance': cell_name
            })
        
        # Create DataFrame from records
        self.logical_db_df = pd.DataFrame(logical_db_records)
    
    def _build_netlist_graph(self, module_data: Dict):
        """
        Build graph representation of the netlist using pandas DataFrames.
        
        This creates:
        1. cell_connections_df: DataFrame with all cell-port-net connections
        2. net_to_cells_df: DataFrame mapping nets to connected cells
        
        Args:
            module_data: The data dictionary for the top module
        """
        cells = module_data.get('cells', {})
        
        # Build records for cell_connections_df
        cell_connections_records = []
        net_to_cells_records = []
        
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
                        # Add to cell_connections DataFrame
                        cell_connections_records.append({
                            'cell_name': cell_name,
                            'cell_type': cell_type,
                            'port': port_name,
                            'net_bit': net_bit,
                            'direction': direction
                        })
                        
                        # Add to net_to_cells DataFrame
                        net_to_cells_records.append({
                            'net_bit': net_bit,
                            'cell_name': cell_name
                        })
        
        # Create DataFrames
        self.cell_connections_df = pd.DataFrame(cell_connections_records)
        self.net_to_cells_df = pd.DataFrame(net_to_cells_records)
        
        # Remove duplicates from net_to_cells_df (same net can connect same cell multiple times via different ports)
        self.net_to_cells_df = self.net_to_cells_df.drop_duplicates()
    
    def get_cells_by_type(self, cell_type: str) -> pd.DataFrame:
        """
        Get all cell instances of a specific type.
        
        Args:
            cell_type: The cell type to query
            
        Returns:
            DataFrame with cell instances of the specified type
        """
        if self.logical_db_df is None:
            return pd.DataFrame(columns=['cell_type', 'cell_instance'])
        return self.logical_db_df[self.logical_db_df['cell_type'] == cell_type]
    
    def get_cell_connections(self, cell_name: str) -> pd.DataFrame:
        """
        Get connections for a specific cell.
        
        Args:
            cell_name: Name of the cell instance
            
        Returns:
            DataFrame with columns [cell_name, cell_type, port, net_bit, direction]
        """
        if self.cell_connections_df is None:
            return pd.DataFrame(columns=['cell_name', 'cell_type', 'port', 'net_bit', 'direction'])
        return self.cell_connections_df[self.cell_connections_df['cell_name'] == cell_name]
    
    def get_net_connections(self, net_bit: int) -> pd.DataFrame:
        """
        Get all cells connected to a specific net.
        
        Args:
            net_bit: The net bit number
            
        Returns:
            DataFrame with cell names connected to this net
        """
        if self.net_to_cells_df is None:
            return pd.DataFrame(columns=['net_bit', 'cell_name'])
        return self.net_to_cells_df[self.net_to_cells_df['net_bit'] == net_bit]
    
    def get_all_cell_types(self) -> List[str]:
        """Get a list of all cell types in the design."""
        if self.logical_db_df is None:
            return []
        return self.logical_db_df['cell_type'].unique().tolist()
    
    def get_total_cell_count(self) -> int:
        """Get the total number of cells in the design."""
        if self.logical_db_df is None:
            return 0
        return len(self.logical_db_df)
    
    def get_cell_type_counts(self) -> pd.Series:
        """
        Get count of each cell type.
        
        Returns:
            Series with cell_type as index and counts as values
        """
        if self.logical_db_df is None:
            return pd.Series(dtype=int)
        return self.logical_db_df['cell_type'].value_counts()
    
    def get_logical_db_dict(self) -> Dict[str, List[str]]:
        """
        Get logical_db as a dictionary for backward compatibility.
        
        Returns:
            Dictionary mapping cell_type to list of cell_instance names
        """
        if self.logical_db_df is None:
            return {}
        
        result = {}
        for cell_type in self.logical_db_df['cell_type'].unique():
            result[cell_type] = self.logical_db_df[
                self.logical_db_df['cell_type'] == cell_type
            ]['cell_instance'].tolist()
        
        return result


def parse_netlist(json_file_path: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Convenience function to parse a netlist JSON file.
    
    Args:
        json_file_path: Path to the [design_name]_mapped.json file
        
    Returns:
        Tuple of (logical_db, netlist_graph) where:
        - logical_db: DataFrame with columns [cell_type, cell_instance]
        - netlist_graph: DataFrame with columns [cell_name, cell_type, port, net_bit, direction]
    """
    parser = NetlistParser(json_file_path)
    return parser.parse()


if __name__ == "__main__":
    # Example usage
    import sys
    from pathlib import Path
    
    if len(sys.argv) < 2:
        print("Usage: python netlist_parser.py <path_to_mapped.json>")
        sys.exit(1)
    
    json_path = sys.argv[1]
    logical_db, netlist_graph = parse_netlist(json_path)
    
    print("Logical Database (cells grouped by type):")
    print("=" * 60)
    cell_counts = logical_db['cell_type'].value_counts()
    for cell_type, count in cell_counts.items():
        print(f"{cell_type}: {count} instances")
    
    print(f"\nTotal cells: {len(logical_db)}")
    print(f"\nNetlist Graph (connectivity):")
    print(f"Total connections: {len(netlist_graph)}")
    print(f"Unique nets: {netlist_graph['net_bit'].nunique()}")
    print(f"Unique cells: {netlist_graph['cell_name'].nunique()}")
    
    # # Show sample of DataFrames
    # print("\n" + "=" * 60)
    # print("Sample logical_db:")
    # print(logical_db)
    
    print("\n" + "=" * 60)
    print("Sample netlist_graph:")
    print(netlist_graph)
    
    # Save netlist_graph to CSV in the parser folder
    parser_dir = Path(__file__).parent
    json_file = Path(json_path)
    csv_filename = parser_dir / f"{json_file.stem}_netlist_graph.csv"
    netlist_graph.to_csv(csv_filename, index=False)
    print(f"\n✓ Netlist graph saved to: {csv_filename}")

