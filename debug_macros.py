
import sys
import os
sys.path.insert(0, os.getcwd())
from src.parsers.fabric_parser import parse_fabric_file_cached

def debug_macros(fabric_path):
    print(f"Loading fabric from {fabric_path}")
    fabric, _ = parse_fabric_file_cached(fabric_path)
    
    tile_def = getattr(fabric, 'tile_definition', {}) or {}
    cells = tile_def.get('cells', []) or []
    print(f"Found {len(cells)} cells in tile_definition.")
    
    macro_map = {}
    for cell in cells:
        try:
            t_name = cell['template_name']
            c_type = cell['cell_type']
            macro_map[t_name] = c_type
        except:
            continue
            
    # Check specific keys
    keys_to_check = ["R0_TAP_0", "R1_BUF_0", "R3_BUF_0", "T18Y73__R3_BUF_0"]
    for k in keys_to_check:
        if k in macro_map:
            print(f"Key '{k}' -> '{macro_map[k]}'")
        else:
            print(f"Key '{k}' NOT FOUND in macro_map")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_macros.py <fabric.yaml>")
    else:
        debug_macros(sys.argv[1])
