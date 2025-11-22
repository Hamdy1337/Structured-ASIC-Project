"""
placement_validator.py: Validation functions for placement correctness.
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Set, Any, Optional
import pandas as pd

from src.placement.placement_utils import hpwl_for_nets, fixed_points_from_pins, nets_by_cell


class PlacementValidationResult:
    """Result of placement validation."""
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.stats: Dict[str, Any] = {}
        self.passed: bool = True

    def add_error(self, msg: str) -> None:
        """Add an error message."""
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        """Add a warning message."""
        self.warnings.append(msg)

    def add_stat(self, key: str, value: Any) -> None:
        """Add a statistic."""
        self.stats[key] = value


def validate_placement(
    placement_df: pd.DataFrame,
    netlist_graph: pd.DataFrame,
    sites_df: pd.DataFrame,
    assignments_df: pd.DataFrame,
    ports_df: pd.DataFrame,
    pins_df: pd.DataFrame,
    updated_pins: pd.DataFrame,
    fabric_df: pd.DataFrame,
) -> PlacementValidationResult:
    """
    Comprehensive validation of placement correctness.
    
    Args:
        placement_df: DataFrame with cell placements (cell_name, site_id, x_um, y_um)
        netlist_graph: DataFrame with netlist connectivity
        sites_df: DataFrame with available sites
        assignments_df: DataFrame with port-to-pin assignments
        ports_df: DataFrame with port information
        pins_df: Original pins DataFrame
        updated_pins: Updated pins DataFrame with assignments
        fabric_df: Fabric DataFrame for cell type checking
    
    Returns:
        PlacementValidationResult with validation status
    """
    result = PlacementValidationResult()
    
    # ===== PHASE 1: BASIC CORRECTNESS CHECKS =====
    print("\n[VALIDATION] === PHASE 1: BASIC CORRECTNESS CHECKS ===")
    
    # 1.1 All cells placed
    unique_cells = set(netlist_graph['cell_name'].unique())
    placed_cells = set(placement_df['cell_name'])
    missing = unique_cells - placed_cells
    if missing:
        result.add_error(f"{len(missing)} cells not placed")
        if len(missing) <= 10:
            result.add_error(f"Missing cells: {sorted(list(missing))}")
        else:
            result.add_error(f"Missing cells (first 10): {sorted(list(missing))[:10]}")
    else:
        print(f"✓ All {len(unique_cells)} cells placed")
    result.add_stat("total_cells", len(unique_cells))
    result.add_stat("placed_cells", len(placed_cells))
    result.add_stat("missing_cells", len(missing))
    
    # 1.2 No duplicate site assignments
    duplicate_sites = placement_df[placement_df.duplicated(subset=['site_id'], keep=False)]
    if len(duplicate_sites) > 0:
        result.add_error(f"{len(duplicate_sites)} duplicate site assignments found")
        result.add_error(f"Duplicate sites: {duplicate_sites[['cell_name', 'site_id']].to_dict('records')[:5]}")
    else:
        print("✓ No duplicate site assignments")
    result.add_stat("duplicate_sites", len(duplicate_sites))
    
    # 1.3 All sites are valid
    invalid_sites = set(placement_df['site_id']) - set(sites_df['site_id'])
    if invalid_sites:
        result.add_error(f"{len(invalid_sites)} invalid site IDs found")
        result.add_error(f"Invalid sites: {list(invalid_sites)[:10]}")
    else:
        print("✓ All site IDs are valid")
    result.add_stat("invalid_sites", len(invalid_sites))
    
    # 1.4 Cell type compatibility (if applicable)
    if 'cell_type' in netlist_graph.columns and 'cell_type' in sites_df.columns:
        type_mismatches = []
        # Build cell type mapping from netlist
        cell_type_map = {}
        for cell_name in placement_df['cell_name'].unique():
            cell_rows = netlist_graph[netlist_graph['cell_name'] == cell_name]
            if len(cell_rows) > 0 and 'cell_type' in cell_rows.columns:
                cell_type_vals = cell_rows['cell_type'].dropna().unique()
                if len(cell_type_vals) > 0:
                    cell_type_map[cell_name] = str(cell_type_vals[0])
        
        # Check each placement
        for _, row in placement_df.iterrows():
            cell_name = row['cell_name']
            site_id = row['site_id']
            
            if cell_name in cell_type_map:
                cell_type = cell_type_map[cell_name]
                site_type = sites_df[sites_df['site_id'] == site_id]['cell_type']
                if len(site_type) > 0:
                    site_type_val = str(site_type.iloc[0])
                    if pd.notna(site_type_val) and cell_type != site_type_val:
                        type_mismatches.append({
                            'cell_name': cell_name,
                            'cell_type': cell_type,
                            'site_id': site_id,
                            'site_type': site_type_val
                        })
        
        if type_mismatches:
            result.add_error(f"{len(type_mismatches)} cell type mismatches found")
            result.add_error(f"Mismatches (first 5): {type_mismatches[:5]}")
        else:
            print("✓ All cell types match site types")
        result.add_stat("type_mismatches", len(type_mismatches))
    else:
        print("⚠ Cell type checking skipped (missing columns)")
        result.add_warning("Cell type checking not performed")
    
    # ===== PHASE 2: PORT-TO-PIN ASSIGNMENT CHECKS =====
    print("\n[VALIDATION] === PHASE 2: PORT-TO-PIN ASSIGNMENT CHECKS ===")
    
    # 2.1 All ports assigned (check updated_pins for assigned ports)
    if not assignments_df.empty:
        assigned_port_count = len(assignments_df)
        total_port_count = len(ports_df)
        if assigned_port_count < total_port_count:
            unassigned = total_port_count - assigned_port_count
            result.add_warning(f"{unassigned} ports not assigned to pins")
        else:
            print(f"✓ All {total_port_count} ports assigned")
        result.add_stat("assigned_ports", assigned_port_count)
        result.add_stat("total_ports", total_port_count)
    else:
        result.add_warning("No port assignments found")
    
    # 2.2 Direction matching
    if not assignments_df.empty and 'direction' in assignments_df.columns:
        direction_mismatches = []
        for _, assign in assignments_df.iterrows():
            port_name = assign.get('port_name', '')
            pin_idx = assign.get('pin_index')
            assign_dir = assign.get('direction', '').lower()
            
            # Get port direction
            port_rows = ports_df[ports_df['port_name'] == port_name] if 'port_name' in ports_df.columns else pd.DataFrame()
            if len(port_rows) > 0 and 'direction' in port_rows.columns:
                port_dir = str(port_rows['direction'].iloc[0]).lower()
                
                # Get pin direction
                if pin_idx is not None and pin_idx in pins_df.index:
                    pin_dir = str(pins_df.at[pin_idx, 'direction']).lower()
                    
                    # Check if directions match (account for oeb special case)
                    if assign_dir != port_dir and assign_dir != pin_dir:
                        direction_mismatches.append({
                            'port': port_name,
                            'port_dir': port_dir,
                            'pin_dir': pin_dir,
                            'assign_dir': assign_dir
                        })
        
        if direction_mismatches:
            result.add_warning(f"{len(direction_mismatches)} direction mismatches found")
        else:
            print("✓ Port-pin direction matching verified")
        result.add_stat("direction_mismatches", len(direction_mismatches))
    
    # 2.3 No duplicate pin assignments
    if not assignments_df.empty and 'pin_index' in assignments_df.columns:
        duplicate_pins = assignments_df[assignments_df.duplicated(subset=['pin_index'], keep=False)]
        if len(duplicate_pins) > 0:
            result.add_error(f"{len(duplicate_pins)} duplicate pin assignments found")
        else:
            print("✓ No duplicate pin assignments")
        result.add_stat("duplicate_pins", len(duplicate_pins))
    
    # ===== PHASE 3: COORDINATE VALIDATION =====
    print("\n[VALIDATION] === PHASE 3: COORDINATE VALIDATION ===")
    
    # Build set of valid coordinates from fabric_df
    coord_mismatches = []
    site_id_mismatches = []
    
    if "cell_x" in fabric_df.columns and "cell_y" in fabric_df.columns:
        # Build set of valid coordinates from fabric YAML
        valid_coords = set()
        for _, row in fabric_df.iterrows():
            if pd.notna(row.get("cell_x")) and pd.notna(row.get("cell_y")):
                try:
                    x = float(row["cell_x"])
                    y = float(row["cell_y"])
                    valid_coords.add((x, y))
                except (ValueError, TypeError):
                    continue
        
        print(f"[DEBUG] Found {len(valid_coords)} valid coordinate pairs in fabric YAML")
        
        # Check each placement coordinate
        for _, row in placement_df.iterrows():
            site_id = row.get("site_id")
            placed_x = row.get("x_um")
            placed_y = row.get("y_um")
            
            if pd.isna(placed_x) or pd.isna(placed_y):
                coord_mismatches.append({
                    'cell_name': row.get('cell_name'),
                    'site_id': site_id,
                    'error': 'missing_coordinates'
                })
                continue
            
            placed_x = float(placed_x)
            placed_y = float(placed_y)
            
            # Verify coordinate exists in fabric
            if (placed_x, placed_y) not in valid_coords:
                coord_mismatches.append({
                    'cell_name': row.get('cell_name'),
                    'site_id': site_id,
                    'x_um': placed_x,
                    'y_um': placed_y,
                    'error': 'coordinate_not_in_fabric'
                })
            
            # Verify site_id maps to correct coordinates in sites_df
            # Note: site_id is used as DataFrame index (0, 1, 2, ...) after reset_index
            if site_id is not None:
                try:
                    # Check if site_id is a valid index
                    if site_id in sites_df.index:
                        site_x = float(sites_df.at[site_id, "x_um"])
                        site_y = float(sites_df.at[site_id, "y_um"])
                        # Allow small floating point tolerance (1e-6 microns = 0.001 nanometers)
                        if abs(placed_x - site_x) > 1e-6 or abs(placed_y - site_y) > 1e-6:
                            site_id_mismatches.append({
                                'cell_name': row.get('cell_name'),
                                'site_id': site_id,
                                'placement_coord': (placed_x, placed_y),
                                'site_coord': (site_x, site_y),
                                'x_diff': abs(placed_x - site_x),
                                'y_diff': abs(placed_y - site_y),
                                'error': 'site_id_coordinate_mismatch'
                            })
                    else:
                        # site_id not in index - check if it's in the site_id column
                        if "site_id" in sites_df.columns:
                            site_rows = sites_df[sites_df["site_id"] == site_id]
                            if len(site_rows) == 0:
                                site_id_mismatches.append({
                                    'cell_name': row.get('cell_name'),
                                    'site_id': site_id,
                                    'error': 'site_id_not_found'
                                })
                            elif len(site_rows) == 1:
                                site_row = site_rows.iloc[0]
                                site_x = float(site_row["x_um"])
                                site_y = float(site_row["y_um"])
                                if abs(placed_x - site_x) > 1e-6 or abs(placed_y - site_y) > 1e-6:
                                    site_id_mismatches.append({
                                        'cell_name': row.get('cell_name'),
                                        'site_id': site_id,
                                        'placement_coord': (placed_x, placed_y),
                                        'site_coord': (site_x, site_y),
                                        'x_diff': abs(placed_x - site_x),
                                        'y_diff': abs(placed_y - site_y),
                                        'error': 'site_id_coordinate_mismatch'
                                    })
                        else:
                            site_id_mismatches.append({
                                'cell_name': row.get('cell_name'),
                                'site_id': site_id,
                                'error': 'site_id_not_in_index_and_no_column'
                            })
                except (KeyError, IndexError, ValueError, TypeError) as e:
                    site_id_mismatches.append({
                        'cell_name': row.get('cell_name'),
                        'site_id': site_id,
                        'error': f'invalid_site_id: {str(e)}'
                    })
        
        if coord_mismatches:
            result.add_error(f"{len(coord_mismatches)} coordinate mismatches found (coordinates not in fabric YAML)")
            if len(coord_mismatches) <= 10:
                result.add_error(f"Coordinate mismatches: {coord_mismatches}")
            else:
                result.add_error(f"Coordinate mismatches (first 10): {coord_mismatches[:10]}")
        else:
            print(f"✓ All {len(placement_df)} placement coordinates exist in fabric YAML")
        
        if site_id_mismatches:
            result.add_error(f"{len(site_id_mismatches)} site_id coordinate mismatches found")
            if len(site_id_mismatches) <= 10:
                result.add_error(f"Site ID mismatches: {site_id_mismatches}")
            else:
                result.add_error(f"Site ID mismatches (first 10): {site_id_mismatches[:10]}")
        else:
            print(f"✓ All {len(placement_df)} site_id mappings are correct")
        
        result.add_stat("coord_mismatches", len(coord_mismatches))
        result.add_stat("site_id_mismatches", len(site_id_mismatches))
        result.add_stat("valid_fabric_coords", len(valid_coords))
    else:
        result.add_warning("Cannot validate coordinates: cell_x/cell_y not in fabric_df")
        result.add_stat("coord_mismatches", 0)
        result.add_stat("site_id_mismatches", 0)
    
    # ===== PHASE 4: QUALITY METRICS =====
    print("\n[VALIDATION] === PHASE 4: QUALITY METRICS ===")
    
    # 3.1 HPWL calculation
    try:
        # Build necessary data structures
        pos_cells: Dict[str, Tuple[float, float]] = {}
        for _, row in placement_df.iterrows():
            pos_cells[row['cell_name']] = (row['x_um'], row['y_um'])
        
        cell_to_nets = nets_by_cell(netlist_graph)
        fixed_pts = fixed_points_from_pins(updated_pins)
        
        # Get all nets
        all_nets: Set[int] = set()
        for nets in cell_to_nets.values():
            all_nets |= nets
        all_nets |= set(fixed_pts.keys())
        
        # Calculate total HPWL
        total_hpwl = hpwl_for_nets(all_nets, pos_cells, cell_to_nets, fixed_pts)
        result.add_stat("total_hpwl", total_hpwl)
        print(f"✓ Total HPWL: {total_hpwl:.2f} microns")
    except Exception as e:
        result.add_warning(f"HPWL calculation failed: {str(e)}")
    
    # 3.2 Placement density/utilization
    site_utilization = (len(placement_df) / len(sites_df)) * 100 if len(sites_df) > 0 else 0.0
    result.add_stat("site_utilization_percent", site_utilization)
    print(f"✓ Site utilization: {site_utilization:.1f}% ({len(placement_df)}/{len(sites_df)} sites)")
    
    # 3.3 Spatial distribution
    if len(placement_df) > 0:
        x_min = placement_df['x_um'].min()
        x_max = placement_df['x_um'].max()
        y_min = placement_df['y_um'].min()
        y_max = placement_df['y_um'].max()
        x_span = x_max - x_min
        y_span = y_max - y_min
        result.add_stat("x_span", x_span)
        result.add_stat("y_span", y_span)
        result.add_stat("placement_bounds", {
            'x_min': x_min, 'x_max': x_max,
            'y_min': y_min, 'y_max': y_max
        })
        print(f"✓ Placement bounds: X=[{x_min:.1f}, {x_max:.1f}] ({x_span:.1f}μm), Y=[{y_min:.1f}, {y_max:.1f}] ({y_span:.1f}μm)")
    
    return result


def print_validation_report(result: PlacementValidationResult) -> None:
    """Print a formatted validation report."""
    print("\n" + "=" * 80)
    print("PLACEMENT VALIDATION REPORT")
    print("=" * 80)
    
    if result.passed:
        print("✅ VALIDATION PASSED")
    else:
        print("❌ VALIDATION FAILED")
    
    if result.errors:
        print(f"\n❌ ERRORS ({len(result.errors)}):")
        for i, error in enumerate(result.errors, 1):
            print(f"  {i}. {error}")
    
    if result.warnings:
        print(f"\n⚠️  WARNINGS ({len(result.warnings)}):")
        for i, warning in enumerate(result.warnings, 1):
            print(f"  {i}. {warning}")
    
    if result.stats:
        print(f"\n📊 STATISTICS:")
        for key, value in result.stats.items():
            if isinstance(value, (int, float)):
                if 'percent' in key.lower() or 'utilization' in key.lower():
                    print(f"  {key}: {value:.1f}%")
                elif 'hpwl' in key.lower():
                    print(f"  {key}: {value:.2f} microns")
                else:
                    print(f"  {key}: {value}")
            else:
                print(f"  {key}: {value}")
    
    print("=" * 80)
    
    if not result.passed:
        print("\n❌ PLACEMENT VALIDATION FAILED - Please review errors above")
    else:
        print("\n✅ PLACEMENT VALIDATION PASSED - All basic checks successful")

