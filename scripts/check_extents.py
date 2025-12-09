import yaml
import sys
import os

# Add the project root to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from src.parsers.fabric_cells_parser import parse_fabric_cells_file
from src.parsers.pins_parser import load_and_validate

def check_extents(path):
    print(f"Checking extents for {path}")
    fabric_cells, _ = parse_fabric_cells_file(path)
    
    max_x = 0
    max_y = 0
    min_x = float('inf')
    min_y = float('inf')
    
    for tile in fabric_cells.tiles.values():
        for cell in tile.cells:
            # cell.x and y are top-left or center? Usually bottom-left in LEF/DEF terms.
            # But we should look for the max coordinate.
            # Assuming these are locations.
            if cell.x > max_x: max_x = cell.x
            if cell.y > max_y: max_y = cell.y
            if cell.x < min_x: min_x = cell.x
            if cell.y < min_y: min_y = cell.y
            
    print(f"Min X: {min_x}, Min Y: {min_y}")
    print(f"Max X: {max_x}, Max Y: {max_y}")
    
    # Check T10Y15 specifically
    print("\nChecking T10Y15 area:")
    for tile_name, tile in fabric_cells.tiles.items():
        if "T10Y15" in tile_name or any("T10Y15" in c.name for c in tile.cells):
             for cell in tile.cells:
                 if "T10Y15" in cell.name:
                     print(f"  {cell.name}: ({cell.x}, {cell.y})")
                     if cell.x * 1000 > 1003600 or cell.y * 1000 > 989200:
                         print("    -> OUTSIDE HARDCODED DIE AREA")

if __name__ == "__main__":
    check_extents("inputs/Platform/fabric_cells.yaml")
