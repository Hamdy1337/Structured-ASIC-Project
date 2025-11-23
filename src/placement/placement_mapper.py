"""
placement_mapper.py: Utilities for mapping placement coordinates to physical cell names
and generating .map files for CTS.

This module handles:
- Mapping placement coordinates to physical fabric cell names
- Extracting cell types from physical names
- Generating .map files in standard format for CTS algorithms
"""

from typing import Dict, List, Tuple, Optional
from pathlib import Path
import pandas as pd

def map_placement_to_physical_cells(
    placement_df: pd.DataFrame,
    fabric_cells_df: pd.DataFrame,
    fabric_df: pd.DataFrame,
    coord_tolerance: float = 0.001
) -> pd.DataFrame:
    """
    Map placement coordinates to physical cell names and add cell types.
    
    Args:
        placement_df: DataFrame with columns ['cell_name', 'x_um', 'y_um', ...]
        fabric_cells_df: DataFrame from parse_fabric_cells_file() with full cell names and coordinates
        fabric_df: DataFrame from get_fabric_db() with template_name->cell_type mapping
        coord_tolerance: Tolerance for coordinate matching in microns (default: 0.001)
    
    Returns:
        DataFrame with added columns 'physical_cell_name' and 'cell_type'
    """
    print(f"[DEBUG] Mapping coordinates to physical cell names...")
    
    # Create coordinate lookup dictionary for fast matching
    coord_to_cell: Dict[Tuple[float, float], Dict[str, str]] = {}
    for _, row in fabric_cells_df.iterrows():
        if 'cell_x' in row and 'cell_y' in row and 'cell_name' in row:
            x = float(row['cell_x'])
            y = float(row['cell_y'])
            cell_name = str(row['cell_name'])
            # Round to nearest tolerance for matching
            key = (round(x / coord_tolerance) * coord_tolerance, 
                   round(y / coord_tolerance) * coord_tolerance)
            coord_to_cell[key] = {
                'physical_cell_name': cell_name,
                'tile_name': str(row.get('tile_name', '')) if 'tile_name' in row else ''
            }
    
    # Create template_name -> cell_type mapping from fabric_df
    template_to_cell_type: Dict[str, str] = {}
    if 'cell_name' in fabric_df.columns and 'cell_type' in fabric_df.columns:
        # fabric_df has template names (like "R0_NAND_2") mapped to cell_type
        for _, row in fabric_df.drop_duplicates(subset=['cell_name']).iterrows():
            template_name = str(row['cell_name'])
            cell_type = str(row['cell_type'])
            template_to_cell_type[template_name] = cell_type
    
    # Extract cell_type from physical cell name
    def get_cell_type(physical_name: str) -> str:
        """Extract cell type from physical name by looking up template in fabric_df."""
        # Physical name format: TILE__TEMPLATE (e.g., "T1Y84__R0_NAND_2")
        if '__' in physical_name:
            template_name = physical_name.split('__', 1)[1]  # Extract "R0_NAND_2"
            if template_name in template_to_cell_type:
                return template_to_cell_type[template_name]
        
        return 'UNKNOWN'
    
    # Add physical cell name and cell type to placement DataFrame
    physical_cell_names: List[str] = []
    cell_types: List[str] = []
    unmatched_count = 0
    
    for _, row in placement_df.iterrows():
        x = float(row['x_um'])
        y = float(row['y_um'])
        # Round coordinates for matching
        key = (round(x / coord_tolerance) * coord_tolerance, 
               round(y / coord_tolerance) * coord_tolerance)
        
        if key in coord_to_cell:
            physical_name = coord_to_cell[key]['physical_cell_name']
            physical_cell_names.append(physical_name)
            cell_types.append(get_cell_type(physical_name))
        else:
            # Try nearest neighbor search if exact match fails
            min_dist = float('inf')
            best_match: Optional[Dict[str, str]] = None
            for (fx, fy), cell_info in coord_to_cell.items():
                dist = abs(fx - x) + abs(fy - y)  # Manhattan distance
                if dist < min_dist and dist < 1.0:  # Within 1 micron
                    min_dist = dist
                    best_match = cell_info
            
            if best_match:
                physical_name = best_match['physical_cell_name']
                physical_cell_names.append(physical_name)
                cell_types.append(get_cell_type(physical_name))
            else:
                physical_cell_names.append('UNKNOWN')
                cell_types.append('UNKNOWN')
                unmatched_count += 1
    
    if unmatched_count > 0:
        print(f"[WARNING] Could not match {unmatched_count} cells to physical fabric slots")
    
    # Add columns to placement DataFrame
    result_df = placement_df.copy()
    result_df['physical_cell_name'] = physical_cell_names
    result_df['cell_type'] = cell_types
    
    return result_df


def generate_map_file(
    placement_df: pd.DataFrame,
    map_file_path: Path,
    design_name: str
) -> None:
    """
    Generate a .map file in standard format for CTS algorithms.
    
    Format:
        # Header comments
        logical_cell_name physical_cell_name
    
    Args:
        placement_df: DataFrame with columns ['cell_name', 'physical_cell_name']
        map_file_path: Path where .map file should be written
        design_name: Name of the design (for header comments)
    """
    print(f"[DEBUG] Generating .map file...")
    
    map_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(map_file_path, 'w') as f:
        # Write header comment
        f.write(f"# Placement mapping file for {design_name}\n")
        f.write("# Format: logical_cell_name physical_cell_name\n")
        f.write(f"# Generated by placement_mapper.py\n\n")
        
        # Write mappings
        for _, row in placement_df.iterrows():
            logical_name = str(row['cell_name'])
            physical_name = str(row['physical_cell_name'])
            if physical_name != 'UNKNOWN':
                f.write(f"{logical_name} {physical_name}\n")
    
    print(f"Map file written to: {map_file_path}")

