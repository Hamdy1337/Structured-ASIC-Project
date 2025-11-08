import sys
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass

# Add project root to Python path for absolute imports
project_root = Path(__file__).parent.parent.parent  # Goes from src/validation/validator.py to project root
sys.path.insert(0, str(project_root))

# Import the parsers
from src.parsers.fabric_db import get_fabric_db
from src.parsers.netlist_parser import parse_netlist


@dataclass
class ValidationResult:
    """Result of design validation."""
    passed: bool
    failed_types: List[Dict[str, Any]]
    fabric_counts: pd.Series
    logical_counts: pd.Series
    all_cell_types: set
    cell_type_to_template: Dict[str, str]


def _build_template_mapping(fabric_db: pd.DataFrame) -> Dict[str, str]:
    """Build cell_type to template name mapping from fabric_db."""
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
    return cell_type_to_template


def validate_design(fabric_db: pd.DataFrame, logical_db: pd.DataFrame) -> ValidationResult:
    """
    Validate if a design can be implemented on the fabric.
    
    Args:
        fabric_db: DataFrame with fabric cell slots (must have 'cell_type' column)
        logical_db: DataFrame with logical cells (must have 'cell_type' column)
    
    Returns:
        ValidationResult with validation status and data for reporting
    """
    fabric_counts = fabric_db['cell_type'].value_counts()
    logical_counts = logical_db['cell_type'].value_counts()
    
    # Get all unique cell types (from both fabric and logical)
    all_cell_types = set(fabric_counts.index) | set(logical_counts.index)
    
    # Check if design fits on fabric
    validation_failed = False
    failed_types = []
    
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
    
    # Build cell_type to template name mapping
    cell_type_to_template = _build_template_mapping(fabric_db)
    
    return ValidationResult(
        passed=not validation_failed,
        failed_types=failed_types,
        fabric_counts=fabric_counts,
        logical_counts=logical_counts,
        all_cell_types=all_cell_types,
        cell_type_to_template=cell_type_to_template
    )


def print_validation_report(result: ValidationResult):
    """
    Print the validation utilization report.
    
    Args:
        result: ValidationResult from validate_design()
    """
    print("=" * 80)
    print("FABRIC UTILIZATION REPORT")
    print("=" * 80)
    print(f"{'Template':<10} {'Cell Type':<32} {'Required':<12} {'Available':<12} {'Utilization':<12} {'Status'}")
    print("-" * 80)
    
    for cell_type in sorted(result.all_cell_types):
        logical_count = result.logical_counts.get(cell_type, 0)
        fabric_count = result.fabric_counts.get(cell_type, 0)
        
        # Get template name for this cell type
        template_name = result.cell_type_to_template.get(cell_type, "N/A")
        
        if fabric_count > 0:
            utilization = (logical_count / fabric_count) * 100
            status = "✓ OK" if logical_count <= fabric_count else "✗ FAIL"
        else:
            utilization = 0.0 if logical_count == 0 else float('inf')
            status = "✗ FAIL" if logical_count > 0 else "✓ OK"
        
        print(f"{template_name:<10} {cell_type:<32} {logical_count:<12} {fabric_count:<12} {utilization:>10.1f}% {status}")
    
    print("=" * 80)
    
    if not result.passed:
        print(f"==============VERDICT: FAILED==============")
        print("\n ✗ VALIDATION FAILED!")
        print("✗ This design cannot be implemented on this fabric.")
        print("\nCell types with insufficient slots:")
        for fail in result.failed_types:
            print(f"  {fail['type']}: Need {fail['required']}, have {fail['available']} (shortage: {fail['shortage']})")
    else:
        print(f"==============VERDICT: PASSED==============")
        print("\n✓ VALIDATION PASSED!")
        print("✓ This design can be implemented on this fabric.")


if __name__ == "__main__":
    # Load fabric database (available slots)
    fabric_db = get_fabric_db(
        str(project_root / "inputs" / "Platform" / "fabric.yaml"),
        str(project_root / "inputs" / "Platform" / "fabric_cells.yaml")
    )
    
    # Load logical database (required cells)
    if len(sys.argv) > 1:
        design_name = sys.argv[1]
        logical_db, netlist_graph = parse_netlist(
            str(project_root / "inputs" / "designs" / f"{design_name}_mapped.json")
        )
    else:
        # Default to aes_128 for testing
        logical_db, netlist_graph = parse_netlist(
            str(project_root / "inputs" / "designs" / "aes_128_mapped.json")
        )
    
    # Validate design
    result = validate_design(fabric_db, logical_db)
    
    # Print report
    print_validation_report(result)
    
    # Exit with appropriate code
    sys.exit(0 if result.passed else 1)
    