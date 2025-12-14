#!/usr/bin/env python3
"""Check LEF merge for duplicates and completeness"""

import re
from pathlib import Path

def extract_definitions(filepath):
    """Extract all major definitions from a LEF file"""
    definitions = {
        'VERSION': [],
        'BUSBITCHARS': [],
        'DIVIDERCHAR': [],
        'UNITS': [],
        'MANUFACTURINGGRID': [],
        'PROPERTYDEFINITIONS': [],
        'SITE': [],
        'LAYER': [],
        'VIA': [],
        'VIARULE': [],
        'MACRO': []
    }
    
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        lines = content.split('\n')
    
    for i, line in enumerate(lines):
        line = line.strip()
        
        # Check for VERSION
        if line.startswith('VERSION'):
            definitions['VERSION'].append((i+1, line))
        
        # Check for BUSBITCHARS
        if line.startswith('BUSBITCHARS'):
            definitions['BUSBITCHARS'].append((i+1, line))
        
        # Check for DIVIDERCHAR
        if line.startswith('DIVIDERCHAR'):
            definitions['DIVIDERCHAR'].append((i+1, line))
        
        # Check for UNITS block
        if line.startswith('UNITS'):
            definitions['UNITS'].append((i+1, line))
        
        # Check for MANUFACTURINGGRID
        if line.startswith('MANUFACTURINGGRID'):
            definitions['MANUFACTURINGGRID'].append((i+1, line))
        
        # Check for PROPERTYDEFINITIONS
        if line.startswith('PROPERTYDEFINITIONS'):
            definitions['PROPERTYDEFINITIONS'].append((i+1, line))
        
        # Check for SITE definitions
        if line.startswith('SITE '):
            site_name = line.split()[1] if len(line.split()) > 1 else ''
            definitions['SITE'].append((i+1, site_name))
        
        # Check for LAYER definitions
        if line.startswith('LAYER '):
            layer_name = line.split()[1] if len(line.split()) > 1 else ''
            definitions['LAYER'].append((i+1, layer_name))
        
        # Check for VIA definitions (not VIARULE)
        if line.startswith('VIA ') and not line.startswith('VIARULE'):
            via_parts = line.split()
            via_name = ' '.join(via_parts[1:]) if len(via_parts) > 1 else ''
            definitions['VIA'].append((i+1, via_name))
        
        # Check for VIARULE definitions
        if line.startswith('VIARULE '):
            via_parts = line.split()
            viarule_name = via_parts[1] if len(via_parts) > 1 else ''
            definitions['VIARULE'].append((i+1, viarule_name))
        
        # Check for MACRO definitions
        if line.startswith('MACRO '):
            macro_name = line.split()[1] if len(line.split()) > 1 else ''
            definitions['MACRO'].append((i+1, macro_name))
    
    return definitions

def check_duplicates(definitions, filename):
    """Check for duplicate definitions"""
    print(f"\n{'='*80}")
    print(f"File: {filename}")
    print(f"{'='*80}")
    
    has_duplicates = False
    
    for def_type, items in definitions.items():
        if def_type in ['VERSION', 'BUSBITCHARS', 'DIVIDERCHAR', 'UNITS', 'MANUFACTURINGGRID', 'PROPERTYDEFINITIONS']:
            # These should appear only once
            if len(items) > 1:
                print(f"\n⚠️  DUPLICATE {def_type} found ({len(items)} occurrences):")
                for line_num, content in items:
                    print(f"   Line {line_num}: {content}")
                has_duplicates = True
            elif len(items) == 1:
                print(f"✓ {def_type}: Found (line {items[0][0]})")
            else:
                print(f"✗ {def_type}: NOT FOUND")
        else:
            # SITE, LAYER, VIA, VIARULE, MACRO - check for duplicate names
            names = [item[1] for item in items]
            unique_names = set(names)
            
            if len(names) != len(unique_names):
                print(f"\n⚠️  DUPLICATE {def_type} definitions found:")
                name_count = {}
                for line_num, name in items:
                    if name in name_count:
                        name_count[name].append(line_num)
                    else:
                        name_count[name] = [line_num]
                
                for name, line_nums in name_count.items():
                    if len(line_nums) > 1:
                        print(f"   {name}: {len(line_nums)} occurrences at lines {line_nums}")
                        has_duplicates = True
            else:
                print(f"✓ {def_type}: {len(items)} unique definitions (no duplicates)")
    
    return has_duplicates

def main():
    base_path = Path(r"c:\Users\Dell-\Desktop\AUC\AUC semester 7\Digital Design 2\DD2 Proj\Structred-ASIC-Project\inputs\Platform")
    
    tlef_file = base_path / "sky130_fd_sc_hd.tlef"
    lef_file = base_path / "sky130_fd_sc_hd.lef"
    merged_file = base_path / "sky130_fd_sc_hd_merged.lef"
    
    print("Analyzing LEF file merge...")
    print(f"Technology LEF: {tlef_file.name}")
    print(f"Standard Cell LEF: {lef_file.name}")
    print(f"Merged LEF: {merged_file.name}")
    
    # Extract definitions from all files
    print("\nExtracting definitions from files...")
    tlef_defs = extract_definitions(tlef_file)
    lef_defs = extract_definitions(lef_file)
    merged_defs = extract_definitions(merged_file)
    
    # Check merged file for duplicates
    has_duplicates = check_duplicates(merged_defs, merged_file.name)
    
    # Compare counts
    print(f"\n{'='*80}")
    print("SUMMARY - Definition Counts")
    print(f"{'='*80}")
    print(f"{'Type':<20} {'TLEF':>10} {'LEF':>10} {'Merged':>10} {'Expected':>10} {'Match':>8}")
    print(f"{'-'*80}")
    
    all_match = True
    for def_type in ['SITE', 'LAYER', 'VIA', 'VIARULE', 'MACRO']:
        tlef_count = len(tlef_defs[def_type])
        lef_count = len(lef_defs[def_type])
        merged_count = len(merged_defs[def_type])
        expected_count = tlef_count + lef_count
        match = "✓" if merged_count == expected_count else "✗"
        
        if merged_count != expected_count:
            all_match = False
        
        print(f"{def_type:<20} {tlef_count:>10} {lef_count:>10} {merged_count:>10} {expected_count:>10} {match:>8}")
    
    print(f"\n{'='*80}")
    if has_duplicates:
        print("❌ RESULT: DUPLICATES FOUND - Merge has issues!")
    elif not all_match:
        print("⚠️  RESULT: Item count mismatch - Some definitions may be missing!")
    else:
        print("✅ RESULT: Merge appears successful - No duplicates, all items present!")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    main()
