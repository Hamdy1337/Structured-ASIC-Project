#!/usr/bin/env python3
"""
Debug script to check if ECO map cells match between map and Verilog
"""

# Check a specific missing cell
missing_cell = "$flatten\\CPU.\\uc.$auto$ff.cc:266:slice$47455"

# Load ECO map
print("Loading ECO map...")
eco_map = {}
with open("build/z80/z80_eco.map", "r") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        if len(parts) >= 2:
            logical = parts[0]
            physical = parts[1]
            eco_map[physical] = logical

print(f"ECO map has {len(eco_map)} entries")

# Check if the missing cell's physical location is in the map
physical_name = "T17Y6__R3_DFBBP_0"
if physical_name in eco_map:
    logical_from_map = eco_map[physical_name]
    print(f"✓ Physical '{physical_name}' found in ECO map")
    print(f"  Logical name from map: '{logical_from_map}'")
    print(f"  Expected logical name: '{missing_cell}'")
    print(f"  Match: {logical_from_map == missing_cell}")
else:
    print(f"✗ Physical '{physical_name}' NOT in ECO map")

# Load Verilog and search for the cell
print("\nSearching Verilog...")
found_in_verilog = False
with open("build/z80/z80_final_fixed.v", "r") as f:
    for line_num, line in enumerate(f, 1):
        if "47455" in line and "flatten" in line:
            print(f"Found at line {line_num}: {line.strip()[:100]}")
            found_in_verilog = True
            break

if found_in_verilog:
    print("✓ Cell found in Verilog")
else:
    print("✗ Cell NOT found in Verilog")
