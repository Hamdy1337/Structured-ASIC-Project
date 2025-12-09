#!/usr/bin/env python3
"""
Check if there are any cells in Verilog that are missing from DEF
"""

print("Loading Verilog instances...")
verilog_instances = set()
with open("build/z80/z80_final_fixed.v", "r") as f:
    for line in f:
        line = line.strip()
        if line.startswith("sky130_fd_sc_hd__"):
            # Extract instance name
            parts = line.split()
            if len(parts) >= 2:
                inst_name = parts[1]
                verilog_instances.add(inst_name)

print(f"Found {len(verilog_instances)} instances in Verilog")

print("\nLoading DEF components...")
def_components = set()
with open("build/z80/z80_fixed.def", "r") as f:
    in_components = False
    for line in f:
        line = line.strip()
        if line.startswith("COMPONENTS"):
            in_components = True
            continue
        if line.startswith("END COMPONENTS"):
            break
        if in_components and line.startswith("-"):
            # Parse component: - name celltype ...
            parts = line.split()
            if len(parts) >= 2:
                comp_name = parts[1]
                def_components.add(comp_name)

print(f"Found {len(def_components)} components in DEF")

print("\nFinding cells in Verilog but not in DEF...")
missing_in_def = verilog_instances - def_components
print(f"Missing: {len(missing_in_def)} cells")

if len(missing_in_def) > 0:
    print("\nFirst 10 missing cells:")
    for i, cell in enumerate(list(missing_in_def)[:10]):
        print(f"  {i+1}. {cell}")
        
print("\nFinding cells in DEF but not in Verilog...")        
missing_in_verilog = def_components - verilog_instances
print(f"Extra in DEF: {len(missing_in_verilog)} cells")

if len(missing_in_verilog) > 0:
    print("\nFirst 10 extra cells (likely unused fabric cells):")
    for i, cell in enumerate(list(missing_in_verilog)[:10]):
        print(f"  {i+1}. {cell}")
