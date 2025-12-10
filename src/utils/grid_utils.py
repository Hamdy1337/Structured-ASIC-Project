"""
Grid utilities for ensuring all coordinates align with manufacturing/routing grids.

This module provides functions to snap coordinates to appropriate grids, preventing
DRT-0073 "No access point" errors caused by floating-point inaccuracies.
"""

def snap_to_grid(value_um, grid_um=0.17):
    """
    Snap a coordinate to the manufacturing grid.
    
    Default grid is 170nm (0.17µm), which is a common divisor of:
    - li1 vertical pitch: 0.46µm 
    - met1 horizontal pitch: 0.34µm
    - Standard cell height: often multiples of 0.34µm or similar
    
    This prevents pin access errors by ensuring all coordinates align with
    the routing grid that OpenROAD expects.
    
    Args:
        value_um: Coordinate in micrometers (float)
        grid_um: Grid spacing in micrometers (default 170nm = 0.17µm)
    
    Returns:
        Snapped coordinate in micrometers (float)
    
    Example:
        >>> snap_to_grid(1.234)  
        1.23  # Snapped to nearest 0.17µm interval
        >>> snap_to_grid(5.678, grid_um=0.34)
        5.66  # Snapped to nearest 0.34µm interval
    """
    # Round to nearest grid point
    snapped = round(value_um / grid_um) * grid_um
    # Round to avoid floating point representation issues
    return round(snapped, 6)  # 6 decimals = nm precision


def snap_to_dbu_grid(value_um, grid_um=0.17):
    """
    Snap a coordinate to grid and convert to database units (DBU).
    
    DEF files use database units where 1000 DBU = 1µm.
    This function snaps to grid first, then converts to integer DBU.
    
    Args:
        value_um: Coordinate in micrometers (float)
        grid_um: Grid spacing in micrometers (default 170nm)
    
    Returns:
        Snapped coordinate in database units (int)
    
    Example:
        >>> snap_to_dbu_grid(1.234)
        1230  # Snapped to 1.23µm, then converted to DBU
    """
    snapped_um = snap_to_grid(value_um, grid_um)
    return int(round(snapped_um * 1000))


def align_to_cell_grid(value_um, cell_height_um=2.72):
    """
    Align a Y coordinate to the standard cell grid.
    
    Standard cells in Sky130 have a height of 2.72µm.
    Y coordinates should align to this grid for proper placement.
    
    Args:
        value_um: Y coordinate in micrometers
        cell_height_um: Standard cell height (default 2.72µm for Sky130)
    
    Returns:
        Aligned Y coordinate in micrometers
    """
    return snap_to_grid(value_um, grid_um=cell_height_um)


if __name__ == "__main__":
    # Self-test
    print("Grid Snapping Self-Test:")
    print(f"  snap_to_grid(1.234) = {snap_to_grid(1.234)}")
    print(f"  snap_to_grid(5.678) = {snap_to_grid(5.678)}")
    print(f"  snap_to_dbu_grid(1.234) = {snap_to_dbu_grid(1.234)}")
    print(f"  snap_to_dbu_grid(5.678) = {snap_to_dbu_grid(5.678)}")
    print(f"  align_to_cell_grid(10.5) = {align_to_cell_grid(10.5)}")
