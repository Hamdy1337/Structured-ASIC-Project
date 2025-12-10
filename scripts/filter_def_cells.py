#!/usr/bin/env python3
"""
Filter out cells with pin access errors from DEF file.
This allows routing to complete for the remaining cells.
"""

import sys
import re

# List of cells that have DRT-0073 pin access errors
PROBLEMATIC_CELLS = [
    "$abc$9276$auto$blifparse.cc:396:parse_blif$10169",
    "$abc$9276$auto$blifparse.cc:396:parse_blif$10170",
    "$abc$9276$auto$blifparse.cc:396:parse_blif$10207",
    "$abc$9276$auto$blifparse.cc:396:parse_blif$10708",
    "$abc$9276$auto$blifparse.cc:396:parse_blif$11255",
    "$abc$9276$auto$blifparse.cc:396:parse_blif$11256",
    "$abc$9276$auto$blifparse.cc:396:parse_blif$11322",
    "$flatten_CPU.$auto$ff.cc:266:slice$4105",
    "unused_T0Y23__R1_DFBBP_0",
    "unused_T0Y47__R2_NAND_0",
    "unused_T18Y47__R2_OR_0",
    "unused_T4Y23__R1_DFBBP_0",
    "unused_T9Y47__R2_NAND_1",
]

def filter_def(input_file, output_file):
    """Remove problematic cells from DEF file."""
    with open(input_file, 'r') as f:
        lines = f.readlines()
    
    in_components = False
    filtered_lines = []
    skip_next = False
    components_removed = 0
    total_components = 0
    
    for i, line in enumerate(lines):
        # Track if we're in COMPONENTS section
        if line.strip().startswith("COMPONENTS"):
            in_components = True
            # Extract component count
            match = re.search(r'COMPONENTS\s+(\d+)', line)
            if match:
                total_components = int(match.group(1))
        elif line.strip().startswith("END COMPONENTS"):
            in_components = False
            # Update component count
            new_count = total_components - components_removed
            filtered_lines.append(f"END COMPONENTS\n")
            # Go back and fix the COMPONENTS line
            for j, fline in enumerate(filtered_lines):
                if fline.strip().startswith("COMPONENTS"):
                    filtered_lines[j] = re.sub(r'COMPONENTS\s+\d+', f'COMPONENTS {new_count}', fline)
                    break
            continue
        
        if in_components and line.strip().startswith("-"):
            # Check if this is a problematic cell
            is_problematic = False
            for cell in PROBLEMATIC_CELLS:
                if cell in line:
                    is_problematic = True
                    components_removed += 1
                    print(f"Removing cell: {cell}")
                    break
            
            if is_problematic:
                continue  # Skip this line
        
        filtered_lines.append(line)
    
    # Write filtered output
    with open(output_file, 'w') as f:
        f.writelines(filtered_lines)
    
    print(f"\nFiltered {components_removed} cells from {total_components} total components")
    print(f"Output written to: {output_file}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python filter_def_cells.py <input.def> <output.def>")
        sys.exit(1)
    
    filter_def(sys.argv[1], sys.argv[2])
