"""
leakage_parser.py: Parses Liberty (.lib) files to determine optimal input vectors for minimum leakage power.

This parser extracts 'leakage_power' tables from cell definitions and evaluates the 'when' conditions
to find which input state results in the lowest leakage current/power.
"""

import re
import sys
import json
import itertools
from typing import Dict, List, Tuple, Any, Optional
from pathlib import Path

def parse_liberty_leakage(lib_file_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Parses a liberty file for cell leakage information.
    
    Args:
        lib_file_path: Path to the .lib file
        
    Returns:
        Dictionary of {cell_name: {'pins': [pins], 'leakage_states': [{'value': float, 'when': str}]}}
    """
    cells = {}
    
    with open(lib_file_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Extremely basic regex parser - Liberty is complex, this targets specifically what we need.
    # We look for cell("name") { ... } blocks
    # This might be memory intensive for huge libs, but standard cells libs are usually manageable (10-50MB)
    
    # Identify all cells
    # Pattern: cell ("NAME") { ... }
    # Since regex on nested brackets is hard, we'll scan line by line or use a state machine.
    # A state machine is safer.
    
    current_cell = None
    cell_data = {}
    
    # Regexes
    cell_start_re = re.compile(r'^\s*cell\s*\(\s*"?(\w+)"?\s*\)\s*\{')
    pin_re = re.compile(r'^\s*pin\s*\(\s*"?(\w+)"?\s*\)\s*\{')
    curr_pin = None
    direction_re = re.compile(r'^\s*direction\s*:\s*"?(\w+)"?\s*;')
    
    leakage_group_start_re = re.compile(r'^\s*leakage_power\s*\(\s*\)\s*\{')
    value_re = re.compile(r'^\s*value\s*:\s*([\d\.\-]+)\s*;')
    when_re = re.compile(r'^\s*when\s*:\s*"(.*)"\s*;')
    
    in_cell = False
    in_leakage = False
    in_pin = False
    brace_count = 0
    cell_brace_start = 0
    
    current_leakage = {}
    
    lines = content.split('\n')
    for line in lines:
        line = line.split('//')[0].strip() # remove comments
        if not line: continue
        
        # Check for Cell Start
        m_cell = cell_start_re.match(line)
        if m_cell and not in_cell:
            current_cell = m_cell.group(1)
            in_cell = True
            cell_data = {
                'pins': [],
                'leakage_states': []
            }
            brace_count = 1
            curr_pin = None
            continue
            
        if in_cell:
            # simple brace counting
            brace_count += line.count('{')
            brace_count -= line.count('}')
            
            if brace_count == 0:
                # End of cell
                cells[current_cell] = cell_data
                in_cell = False
                current_cell = None
                continue
                
            # Scan for content inside cell
            
            # Pin detection (to know what inputs exist)
            m_pin = pin_re.match(line)
            if m_pin:
                curr_pin = m_pin.group(1)
                in_pin = True
                continue
                
            if in_pin:
                m_dir = direction_re.match(line)
                if m_dir:
                    direction = m_dir.group(1)
                    if direction == "input":
                        cell_data['pins'].append(curr_pin)
                if line.strip() == "}": # End of pin (approximate, relying on formatting)
                    in_pin = False
                    curr_pin = None
            
            # Leakage detection
            m_leak = leakage_group_start_re.match(line)
            if m_leak:
                in_leakage = True
                current_leakage = {'value': 0.0, 'when': ''}
                continue
                
            if in_leakage:
                m_val = value_re.match(line)
                if m_val:
                    current_leakage['value'] = float(m_val.group(1))
                
                m_when = when_re.match(line)
                if m_when:
                    current_leakage['when'] = m_when.group(1)
                    
                if '}' in line: # End of leakage group
                    if current_leakage.get('when'): # Only keep conditional leakage
                        cell_data['leakage_states'].append(current_leakage)
                    in_leakage = False
    
    return cells

def solve_boolean_vector(condition: str, pins: List[str]) -> Optional[Dict[str, int]]:
    """
    Attempts to solve a boolean condition string (e.g., "!A & B") 
    to find the state vector it represents.
    Returns: {'A': 0, 'B': 1}
    This is a heuristic solver: it tries all combinations of inputs 
    and checks which one satisfies the condition.
    """
    # Replace Liberty operators with Python operators
    # ! -> not, & -> and, | -> or, + -> or, * -> and, ^ -> !=
    # Liberty syntax: A&!B, A*B, A+B
    
    # Normalize operators
    expr = condition.replace('!', ' not ').replace('&', ' and ').replace('*', ' and ').replace('|', ' or ').replace('+', ' or ').replace('^', ' != ')
    
    # Generate all truth table rows for the pins mentioned in condition
    # Find used pins
    used_pins = [p for p in pins if p in condition] # Rough check
    if not used_pins:
        return {} # Constant or unknown vars
    
    # Try all combinations
    n = len(used_pins)
    for bits in itertools.product([0, 1], repeat=n):
        env = {pin: bit for pin, bit in zip(used_pins, bits)}
        
        # Evaluate expression safely
        # We need to construct a context where pins are variables
        try:
            # Safe eval using local dict
            # Replace pin names in expr with values? No, better to put them in locals
            # BEWARE: eval is dangerous, but we control the inputs (lib file)
            # Also need to map pin names to valid python vars if they have weird chars? 
            # Standard cells usually just [A-Z0-9_]
            
            # Since eval works on locals, we can just pass env
            if eval(expr, {}, env):
                return env # Found a satisfying assignment!
        except Exception:
            continue
            
    return None

def get_optimal_leakage_vectors(lib_file_path: str) -> Dict[str, Dict[str, int]]:
    """
    Main function to get optimal vectors.
    Returns: {cell_name: {pin_name: 0/1}}
    """
    print(f"Parsing {lib_file_path}...")
    cells = parse_liberty_leakage(lib_file_path)
    print(f"Found {len(cells)} cells.")
    
    optimal_vectors = {}
    
    for cell_name, data in cells.items():
        if not data['leakage_states']:
            continue
            
        # Find state with minimum leakage value
        # Sort by value
        sorted_states = sorted(data['leakage_states'], key=lambda x: x['value'])
        
        # Take the best one
        best_state = sorted_states[0]
        min_val = best_state['value']
        condition = best_state['when']
        
        # Solve the condition to get the vector
        # We want the vector that MAKES the condition TRUE (because that condition yields min_val)
        pins = data['pins']
        vector = solve_boolean_vector(condition, pins)
        
        if vector:
            optimal_vectors[cell_name] = vector
            
    return optimal_vectors

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Default path for testing in this specific project
        lib_path = r"inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
    else:
        lib_path = sys.argv[1]
        
    if not Path(lib_path).exists():
        print(f"Error: File {lib_path} not found.")
        sys.exit(1)
        
    vectors = get_optimal_leakage_vectors(lib_path)
    
    # Print some interesting samples
    samples = ['sky130_fd_sc_hd__nand2_1', 'sky130_fd_sc_hd__and2_1', 'sky130_fd_sc_hd__inv_1', 'sky130_fd_sc_hd__buf_1']
    print("\nSample Optimal Vectors:")
    for s in samples:
        if s in vectors:
            print(f"{s}: {vectors[s]}")
            
    # Save to JSON
    out_path = Path("inputs") / "leakage_optimal_vectors.json"
    with open(out_path, 'w') as f:
        json.dump(vectors, f, indent=2)
        
    print(f"\nGeneratd optimal vectors for {len(vectors)} cells.")
    print(f"Saved to: {out_path}")
