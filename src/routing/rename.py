import os
import sys
import re

def rename_instances(design_name):
    # Paths
    build_dir = os.path.join("build", design_name)
    verilog_path = os.path.join(build_dir, f"{design_name}_final.v")
    map_path = os.path.join(build_dir, f"{design_name}.map")
    output_path = os.path.join(build_dir, f"{design_name}_renamed.v")

    print(f"Processing design: {design_name}")
    print(f"Reading map: {map_path}")
    print(f"Reading verilog: {verilog_path}")
    print(f"Output: {output_path}")

    if not os.path.exists(map_path):
        print(f"Error: Map file not found: {map_path}")
        sys.exit(1)
    if not os.path.exists(verilog_path):
        print(f"Error: Verilog file not found: {verilog_path}")
        sys.exit(1)

    # Load mapping
    # logical -> physical
    # Note: formatting in valid file lines: "logical physical"
    mapping = {}
    with open(map_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            logical, physical = parts
            mapping[logical] = physical

    print(f"Loaded {len(mapping)} mappings.")

    # Regex to find instance instantiations
    # Pattern: 
    #   spaces cell_type spaces instance_name spaces (
    # We want to capture instance_name
    # instance_name can be escaped (starts with \) or simple.
    
    # Simple ident: [a-zA-Z_][a-zA-Z0-9_$]*
    # Escaped ident: \\[^\s]+
    
    # We'll use a broader match for the instance name token: \S+
    # And then check if it's in our map.
    
    # Regex:
    # ^\s* (starts with optional space)
    # [a-zA-Z0-9_$]+ (cell type - assuming simple identifier for cell type, which are usually standard cells)
    # \s+ (at least one space)
    # (\S+) (capture instance name - non-whitespace characters)
    # \s* (optional space)
    # \( (opening parenthesis)
    
    instance_pattern = re.compile(r"^(\s*[a-zA-Z0-9_$]+\s+)(\S+)(\s*\()", re.MULTILINE)

    replaced_count = 0
    
    with open(verilog_path, 'r') as fin, open(output_path, 'w') as fout:
        for line in fin:
            match = instance_pattern.match(line)
            if match:
                prefix = match.group(1)
                raw_instance_name = match.group(2)
                suffix = match.group(3)
                
                # Normalize instance name for lookup
                # If escaped (starts with \), key might be the name without \
                # But typically map file keys are the "internal" names.
                # If map file has "$abc...", in Verilog it appears as "\$abc... " (escaped).
                # So we should strip leading '\' if present to check typical keys.
                # HOWEVER, we must be careful.
                
                lookup_name = raw_instance_name
                if lookup_name.startswith('\\'):
                    lookup_name = lookup_name[1:]
                
                if lookup_name in mapping:
                    physical_name = mapping[lookup_name]
                    # Check if physical name needs escaping? 
                    # Physical names are like TILEX10Y20_NAND2, so no.
                    # Reconstruct line
                    # We keep the raw instance name if we don't replace, 
                    # but if we replace, we use physical_name.
                    
                    # Verilog instance names usually don't need escaping if they are simple.
                    # If physical_name is simple, just use it.
                    
                    new_line = f"{prefix}{physical_name}{suffix}{line[match.end():]}"
                    fout.write(new_line)
                    replaced_count += 1
                else:
                    # Not in map, keep as is (e.g. unused cells or other things)
                    fout.write(line)
            else:
                fout.write(line)

    print(f"Renamed {replaced_count} instances.")
    print("Done.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python rename.py <design_name>")
        sys.exit(1)
    
    rename_instances(sys.argv[1])
