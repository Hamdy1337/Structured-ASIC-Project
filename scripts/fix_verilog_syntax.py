import re
import sys
import os

def fix_verilog(file_path):
    print(f"Reading {file_path}...")
    with open(file_path, 'r') as f:
        content = f.read()

    # Pattern to find identifiers starting with $ that are not already escaped with \
    # We look for $ followed by non-separator characters.
    # Separators: whitespace, (, ), ;, ,
    # We escape them by prepending \ and appending a space.
    
    pattern = r'(?<!\\)(\$[^\s\(\)\;\,]+)'
    
    count = 0
    def replace_func(match):
        nonlocal count
        count += 1
        return "\\" + match.group(1) + " "

    print("Applying fixes...")
    new_content = re.sub(pattern, replace_func, content)
    
    output_path = file_path.replace('.v', '_fixed.v')
    print(f"Writing {output_path}...")
    with open(output_path, 'w') as f:
        f.write(new_content)
    
    print(f"Done. Replaced {count} occurrences.")
    print(f"Output: {output_path}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        fix_verilog(sys.argv[1])
    else:
        # Default path
        fix_verilog("build/z80/z80_final.v")
