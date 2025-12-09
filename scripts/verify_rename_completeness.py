
import sys
import os
import re

def verify_rename(design_name):
    map_file = os.path.join("build", design_name, f"{design_name}.map")
    verilog_file = os.path.join("build", design_name, f"{design_name}_renamed.v")

    print(f"Verifying rename for {design_name}")
    print(f"Map: {map_file}")
    print(f"Verilog: {verilog_file}")

    if not os.path.exists(map_file):
        print("Map file missing.")
        sys.exit(1)
    if not os.path.exists(verilog_file):
        print("Verilog file missing.")
        sys.exit(1)

    # 1. Load Map
    mapping = {}
    with open(map_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = line.split()
            if len(parts) == 2:
                mapping[parts[0]] = parts[1]
    
    print(f"Loaded {len(mapping)} entries from map.")

    with open(verilog_file, 'r') as f:
        content = f.read()

    # 2. Check for presence/absence
    # It's faster to just grep the content than parsing Verilog fully, 
    # ensuring we look for whole words or specific patterns if possible.
    # However, simpler check: 
    # - Logical names should NOT match " instance_name (" pattern.
    # - Physical names SHOULD match " instance_name (" pattern.

    # Let's find all instances in the Verilog first
    # Regex from rename.py: ^(\s*[a-zA-Z0-9_$]+\s+)(\S+)(\s*\()
    # We want group 2.
    
    instance_pattern = re.compile(r"^\s*[a-zA-Z0-9_$]+\s+(\S+)\s*\(", re.MULTILINE)
    
    found_instances = set(instance_pattern.findall(content))
    
    print(f"Found {len(found_instances)} instances definitions in Verilog.")

    # 3. Verify
    missing_phys = []
    remaining_logical = []

    for logical, physical in mapping.items():
        # Logical might be escaped in Verilog if it starts with $? 
        # Usually $abc... is fine as is.
        # But if the map file has "foo" and Verilog has "\foo ", logic needs to handle it.
        # Our rename.py handled escaping by stripping leading \ for lookup.
        # So we should expect PHYSICAL names to be present (usually simple).
        # And LOGICAL names (or their escaped versions) to be ABSENT.
        
        # Check if Logical is present
        # We need to check both "logical" and "\logical " just in case?
        # Actually, rename.py checks `lookup_name` (un-escaped).
        # So if we find `logical` in `found_instances`, that's a failure (unless it was somehow not mapped).
        
        if logical in found_instances:
            remaining_logical.append(logical)
        
        # If logical starts with $, it might be escaped as \$... in Verilog? 
        # But found_instances will contain the raw string from file.
        # If file has "\$abc ", capturing group 2 is "\$abc" (or "\$abc " depending on \S+).
        # \S includes \. 
        # Wait, \S+ matches "\$abc". 
        
        # Let's do a simpler check:
        # Physical name MUST be in found_instances.
        if physical not in found_instances:
            missing_phys.append(physical)

    if not missing_phys and not remaining_logical:
        print("SUCCESS: All mapped instances verified.")
        print(f" - {len(mapping)} physical instances found in Verilog.")
        print(" - 0 logical instances remaining from map.")
    else:
        print("FAILURE: Verification failed.")
        if remaining_logical:
            print(f"ERROR: {len(remaining_logical)} logical instances still present (not renamed):")
            for x in remaining_logical[:5]: print(f"  - {x}")
            if len(remaining_logical) > 5: print("  ...")
        
        if missing_phys:
            print(f"ERROR: {len(missing_phys)} physical instances missing from Verilog:")
            for x in missing_phys[:5]: print(f"  - {x}")
            if len(missing_phys) > 5: print("  ...")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python verify_rename.py <design>")
        sys.exit(1)
    verify_rename(sys.argv[1])
