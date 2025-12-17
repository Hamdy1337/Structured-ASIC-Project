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
    
    # Create template_name -> cell_type mapping from fabric_df
    # fabric_df may have full physical names (like "T0Y3__R0_TAP_0") or just template names
    template_to_cell_type: Dict[str, str] = {}
    if 'cell_name' in fabric_df.columns and 'cell_type' in fabric_df.columns:
        for _, row in fabric_df.drop_duplicates(subset=['cell_name']).iterrows():
            full_name = str(row['cell_name'])
            cell_type = str(row['cell_type'])
            # Extract template from physical name if present
            if '__' in full_name:
                template_name = full_name.split('__', 1)[1]
            else:
                template_name = full_name
            template_to_cell_type[template_name] = cell_type
    
    # Extract cell_type from physical cell name
    def get_cell_type(physical_name: str) -> str:
        """Extract cell type from physical name by looking up template in fabric_df."""
        if '__' in physical_name:
            template_name = physical_name.split('__', 1)[1]
            if template_name in template_to_cell_type:
                return template_to_cell_type[template_name]
        return 'UNKNOWN'
    
    # Build coordinate -> list of cells (there may be multiple cells at same coords with different types)
    coord_to_cells: Dict[Tuple[float, float], List[Dict[str, str]]] = {}
    for _, row in fabric_cells_df.iterrows():
        if 'cell_x' in row and 'cell_y' in row and 'cell_name' in row:
            x = float(row['cell_x'])
            y = float(row['cell_y'])
            cell_name = str(row['cell_name'])
            cell_type = get_cell_type(cell_name)
            key = (round(x / coord_tolerance) * coord_tolerance, 
                   round(y / coord_tolerance) * coord_tolerance)
            if key not in coord_to_cells:
                coord_to_cells[key] = []
            coord_to_cells[key].append({
                'physical_cell_name': cell_name,
                'cell_type': cell_type,
                'tile_name': str(row.get('tile_name', '')) if 'tile_name' in row else ''
            })
    
    # Check if placement_df has cell_type info (from RL placer)
    has_logical_type = 'cell_type' in placement_df.columns
    
    # Add physical cell name and cell type to placement DataFrame
    physical_cell_names: List[str] = []
    cell_types: List[str] = []
    unmatched_count = 0
    type_mismatch_count = 0
    
    for _, row in placement_df.iterrows():
        x = float(row['x_um'])
        y = float(row['y_um'])
        logical_type = str(row.get('cell_type', '')) if has_logical_type else ''
        
        key = (round(x / coord_tolerance) * coord_tolerance, 
               round(y / coord_tolerance) * coord_tolerance)
        
        if key in coord_to_cells:
            candidates = coord_to_cells[key]
            
            # If we have logical type info, prefer matching cell type
            best_match = None
            if logical_type and logical_type != 'UNKNOWN':
                for c in candidates:
                    if c['cell_type'] == logical_type:
                        best_match = c
                        break
            
            # Fall back to first candidate if no type match
            if best_match is None:
                best_match = candidates[0]
                if logical_type and logical_type != 'UNKNOWN' and logical_type != best_match['cell_type']:
                    type_mismatch_count += 1
            
            physical_cell_names.append(best_match['physical_cell_name'])
            cell_types.append(best_match['cell_type'])
        else:
            # Try nearest neighbor search
            min_dist = float('inf')
            best_match = None
            for (fx, fy), cell_list in coord_to_cells.items():
                dist = abs(fx - x) + abs(fy - y)
                if dist < min_dist and dist < 1.0:
                    # Prefer type match for neighbors too
                    for c in cell_list:
                        if logical_type and c['cell_type'] == logical_type:
                            min_dist = dist
                            best_match = c
                            break
                    if best_match is None and dist < min_dist:
                        min_dist = dist
                        best_match = cell_list[0]
            
            if best_match:
                physical_cell_names.append(best_match['physical_cell_name'])
                cell_types.append(best_match['cell_type'])
            else:
                physical_cell_names.append('UNKNOWN')
                cell_types.append('UNKNOWN')
                unmatched_count += 1
    
    if unmatched_count > 0:
        print(f"[WARNING] Could not match {unmatched_count} cells to physical fabric slots")
    if type_mismatch_count > 0:
        print(f"[WARNING] {type_mismatch_count} cells had type mismatch (logical vs physical)")
    
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

