import sys
import re

def fix_def_names(file_path):
    print(f"Reading {file_path}...")
    with open(file_path, 'r') as f:
        content = f.read()
    
    # We want to replace " - \$" with " - $"
    # This removes the backslash escape from the component name start
    # pattern: look for " - \" followed by anything
    # applying global replacement
    
    print("Replacing ' - \\$' with ' - $' ...")
    new_content = content.replace(" - \\$", " - $")
    
    # Also handle other escaped chars if necessary?
    # The warning showed: encoded \$flatten...
    # So replacing " - \$" should fix the start of the name.
    
    output_path = file_path
    print(f"Writing {output_path}...")
    with open(output_path, 'w') as f:
        f.write(new_content)
        
    print("Done.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        fix_def_names(sys.argv[1])
    else:
        fix_def_names("build/z80/z80_fixed.def")
