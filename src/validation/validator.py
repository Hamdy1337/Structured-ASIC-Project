import sys
import pandas as pd
from pathlib import Path

# Add project root to Python path for absolute imports
project_root = Path(__file__).parent.parent.parent  # Goes from src/validation/validator.py to project root
sys.path.insert(0, str(project_root))

# Import the parsers
from src.parsers.fabric_db import get_fabric_db
from src.parsers.netlist_parser import parse_netlist

# Load fabric database (available slots)
fabric_db = get_fabric_db(
    str(project_root / "inputs" / "Platform" / "fabric.yaml"),
    str(project_root / "inputs" / "Platform" / "fabric_cells.yaml")
)

# Load logical database (required cells)
# You'll need to pass the design name as argument
# design_name = sys.argv[1]  # e.g., "arith"
# logical_db, netlist_graph = parse_netlist(
#     str(project_root / "inputs" / "designs" / f"{design_name}_mapped.json")
# )
logical_db, netlist_graph = parse_netlist(
    str(project_root / "inputs" / "designs" / "aes_128_mapped.json")
)

fabric_counts = fabric_db['cell_type'].value_counts()
logical_counts = logical_db['cell_type'].value_counts()



# Check if design fits on fabric
validation_failed = False
failed_types = []

# Get all unique cell types (from both fabric and logical)
all_cell_types = set(fabric_counts.index) | set(logical_counts.index)

for cell_type in all_cell_types:
    logical_count = logical_counts.get(cell_type, 0)
    fabric_count = fabric_counts.get(cell_type, 0)
    
    # If design needs more cells than available
    if logical_count > fabric_count:
        validation_failed = True
        failed_types.append({
            'type': cell_type,
            'required': logical_count,
            'available': fabric_count,
            'shortage': logical_count - fabric_count
        })

# Build cell_type to template name mapping from fabric_db
# Extract template base name from cell_name (e.g., "R0_NAND_0" -> "NAND2")
cell_type_to_template = {}
for cell_type in fabric_db['cell_type'].unique():
    # Get first cell_name for this cell_type
    cell_name = fabric_db[fabric_db['cell_type'] == cell_type]['cell_name'].iloc[0]
    
    # Extract base template name (e.g., "R0_NAND_0" -> "NAND")
    parts = cell_name.split('_')
    if len(parts) >= 2:
        base_template = parts[1]  # "NAND", "OR", "INV", etc.
        
        # For NAND, OR, AND - check if it's 2-input variant
        if base_template in ['NAND', 'OR', 'AND']:
            # Check cell_type for "2" pattern (nand2, or2, and2)
            if f"{base_template.lower()}2" in cell_type.lower():
                base_template = f"{base_template}2"
        
        cell_type_to_template[cell_type] = base_template
# Print utilization report
print("=" * 80)
print("FABRIC UTILIZATION REPORT")
print("=" * 80)
print(f"{'Template':<8} {'Cell Type':<32} {'Required':<12} {'Available':<12} {'Utilization':<12} {'Status'}")
print("-" * 80)

for cell_type in sorted(all_cell_types):
    logical_count = logical_counts.get(cell_type, 0)
    fabric_count = fabric_counts.get(cell_type, 0)
    
    # Get template name for this cell type
    template_name = cell_type_to_template.get(cell_type, "N/A")
    
    if fabric_count > 0:
        utilization = (logical_count / fabric_count) * 100
        status = "✓ OK" if logical_count <= fabric_count else "✗ FAIL"
    else:
        utilization = 0.0 if logical_count == 0 else float('inf')
        status = "✗ FAIL" if logical_count > 0 else "✓ OK"
    
    print(f"{template_name:<10} {cell_type:<32} {logical_count:<12} {fabric_count:<12} {utilization:>10.1f}% { status}")

print("=" * 80)
if validation_failed:
    print(f"==============VERDICT: FAILED==============")
    print("\n ✗ VALIDATION FAILED!")
    print("✗ This design cannot be implemented on this fabric.")
    print("\nCell types with insufficient slots:")
    for fail in failed_types:
        print(f"  {fail['type']}: Need {fail['required']}, have {fail['available']} (shortage: {fail['shortage']})")
    sys.exit(1)
else:
    print(f"==============VERDICT: PASSED==============")
    print("\n✓ VALIDATION PASSED!")
    print("✓ This design can be implemented on this fabric.")
    sys.exit(0)
    