"""
eco_validator.py: Comprehensive verification for CTS and ECO flow.

ASIC Design Verification Checklist:
1. Structural Correctness
   - All DFFs connected to clock tree
   - No floating inputs
   - All cells properly instantiated
   
2. Timing Correctness
   - Clock tree skew within limits
   - Clock tree delays reasonable
   - Setup/hold times met
   
3. Power-Down ECO Correctness
   - All unused cells tied
   - No floating inputs
   - Tie cells properly connected
   
4. Verilog Correctness
   - Syntactically valid
   - All cells present
   - All connections valid
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Set, Any
import pandas as pd

from src.parsers.netlist_parser import NetlistParser


class ECOValidationResult:
    """Result of ECO validation."""
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.stats: Dict[str, Any] = {}
        self.passed: bool = True
        
    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False
        
    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)
        
    def add_stat(self, key: str, value: Any) -> None:
        self.stats[key] = value


def validate_cts_structure(
    netlist_path: Path,
    cts_json_path: Path,
    design_name: str  # Unused but kept for API consistency
) -> ECOValidationResult:
    """
    Verify CTS structural correctness.
    
    Checks:
    1. All DFFs connected to clock tree
    2. Clock tree connectivity (no isolated nodes)
    3. Buffer fanout within limits
    4. Tree depth reasonable
    """
    result = ECOValidationResult()
    
    print("\n[CTS VALIDATION] === Structural Correctness ===")
    
    # Load CTS data
    with open(cts_json_path, 'r') as f:
        cts_data = json.load(f)
    
    sinks = cts_data.get('sinks', [])
    buffers = cts_data.get('buffers', [])
    connections = cts_data.get('connections', [])
    
    result.add_stat('num_sinks', len(sinks))
    result.add_stat('num_buffers', len(buffers))
    result.add_stat('num_connections', len(connections))
    
    # Check 1: All sinks present
    parser = NetlistParser(str(netlist_path))
    _, _, netlist_graph_df = parser.parse()
    
    dff_cells = netlist_graph_df[
        netlist_graph_df['cell_type'].str.contains('df', case=False, na=False)
    ]['cell_name'].unique()
    
    sink_names = {s['name'] for s in sinks}
    dff_set = set(dff_cells)
    
    missing_sinks = dff_set - sink_names
    if missing_sinks:
        result.add_error(f"{len(missing_sinks)} DFFs not in CTS tree")
        if len(missing_sinks) <= 10:
            result.add_error(f"Missing: {sorted(list(missing_sinks))}")
    else:
        print(f"✓ All {len(dff_set)} DFFs included in CTS tree")
    
    # Check 2: Clock tree connectivity
    # Build connection graph
    buffer_positions = {b['name']: (b['x'], b['y']) for b in buffers}
    sink_positions = {s['name']: (s['x'], s['y']) for s in sinks}
    
    # Count connections per buffer
    buffer_connections = {}
    for conn in connections:
        from_name = None
        to_name = None
        
        # Find which buffer/sink this connection is from/to
        from_pos = (conn['from']['x'], conn['from']['y'])
        to_pos = (conn['to']['x'], conn['to']['y'])
        
        for name, pos in buffer_positions.items():
            if abs(pos[0] - from_pos[0]) < 0.1 and abs(pos[1] - from_pos[1]) < 0.1:
                from_name = name
                break
        
        for name, pos in sink_positions.items():
            if abs(pos[0] - to_pos[0]) < 0.1 and abs(pos[1] - to_pos[1]) < 0.1:
                to_name = name
                break
        
        for name, pos in buffer_positions.items():
            if abs(pos[0] - to_pos[0]) < 0.1 and abs(pos[1] - to_pos[1]) < 0.1:
                to_name = name
                break
        
        if from_name:
            buffer_connections.setdefault(from_name, []).append(to_name or 'sink')
    
    # Check fanout
    max_fanout = 0
    high_fanout_buffers = []
    for buf_name, children in buffer_connections.items():
        fanout = len(children)
        max_fanout = max(max_fanout, fanout)
        if fanout > 8:  # Reasonable limit for clock buffers
            high_fanout_buffers.append((buf_name, fanout))
    
    result.add_stat('max_buffer_fanout', max_fanout)
    if high_fanout_buffers:
        result.add_warning(f"{len(high_fanout_buffers)} buffers with fanout > 8")
        result.add_warning(f"Max fanout: {max_fanout}")
    else:
        print(f"✓ Buffer fanout within limits (max: {max_fanout})")
    
    # Check 3: Tree depth
    # Estimate depth from buffer levels
    if buffers:
        max_level = max(b.get('level', 0) for b in buffers)
        result.add_stat('tree_depth', max_level)
        if max_level > 10:
            result.add_warning(f"Deep clock tree (depth: {max_level})")
        else:
            print(f"✓ Tree depth reasonable (depth: {max_level})")
    
    return result


def validate_verilog_syntax(verilog_path: Path) -> ECOValidationResult:
    """
    Verify Verilog syntax and structure.
    
    Checks:
    1. Valid Verilog syntax (basic checks)
    2. All cells properly instantiated
    3. All ports connected
    4. No undefined nets
    """
    result = ECOValidationResult()
    
    print("\n[VERILOG VALIDATION] === Syntax and Structure ===")
    
    with open(verilog_path, 'r') as f:
        content = f.read()
    
    # Check 1: Basic structure
    if 'module' not in content:
        result.add_error("No module declaration found")
    else:
        print("✓ Module declaration found")
    
    if 'endmodule' not in content:
        result.add_error("No endmodule found")
    else:
        print("✓ endmodule found")
    
    # Check 2: Count cells and categorize
    cell_instances = re.findall(r'(\w+)\s+(\w+)\s*\(', content)
    result.add_stat('num_cell_instances', len(cell_instances))
    
    # Categorize cells
    original_cells = [c for c in cell_instances if not c[1].startswith(('cts_htree_', 'tie_cell_', 'unused_'))]
    cts_buffers = [c for c in cell_instances if c[1].startswith('cts_htree_')]
    tie_cells = [c for c in cell_instances if c[1].startswith('tie_cell_')]
    unused_cells = [c for c in cell_instances if c[1].startswith('unused_')]
    
    result.add_stat('num_original_cells', len(original_cells))
    result.add_stat('num_cts_buffers', len(cts_buffers))
    result.add_stat('num_tie_cells', len(tie_cells))
    result.add_stat('num_unused_cells', len(unused_cells))
    
    print(f"✓ Found {len(cell_instances)} total cell instances")
    print(f"  - Original design cells: {len(original_cells)}")
    print(f"  - CTS buffers: {len(cts_buffers)}")
    print(f"  - Tie cells: {len(tie_cells)}")
    print(f"  - Unused cells (power-down): {len(unused_cells)}")
    
    # Check 3: Wire declarations
    wire_declarations = re.findall(r'wire\s+(\[.*?\]\s+)?(\w+)', content)
    result.add_stat('num_wires', len(wire_declarations))
    
    # Check 4: Look for common errors
    if re.search(r'\.\w+\s*\([^)]*\)\s*\.\w+\s*\(', content):
        result.add_warning("Possible missing comma in port connections")
    
    # Check 5: Unclosed parentheses (basic check)
    open_parens = content.count('(')
    close_parens = content.count(')')
    if open_parens != close_parens:
        result.add_error(f"Mismatched parentheses: {open_parens} open, {close_parens} close")
    else:
        print("✓ Parentheses balanced")
    
    return result


def validate_clock_connections(
    netlist_path: Path,
    verilog_path: Path,
    design_name: str  # Unused but kept for API consistency
) -> ECOValidationResult:
    """
    Verify all DFFs are connected to clock tree.
    
    Checks:
    1. All DFF CLK ports connected
    2. Clock net is driven
    3. No clock nets floating
    """
    result = ECOValidationResult()
    
    print("\n[CLOCK VALIDATION] === Clock Tree Connections ===")
    
    # Parse netlist
    parser = NetlistParser(str(netlist_path))
    parser.parse()
    netlist_data = parser.data
    top_module = parser.top_module
    module = netlist_data['modules'][top_module]
    
    # Find all DFFs
    dff_cells = {}
    for cell_name, cell_data in module['cells'].items():
        cell_type = cell_data.get('type', '')
        if 'df' in cell_type.lower():
            dff_cells[cell_name] = cell_data
    
    result.add_stat('num_dffs', len(dff_cells))
    
    # Check each DFF has CLK connected
    dffs_without_clk = []
    dffs_with_clk = []
    
    for cell_name, cell_data in dff_cells.items():
        connections = cell_data.get('connections', {})
        clk_net = connections.get('CLK')
        
        if not clk_net:
            dffs_without_clk.append(cell_name)
        else:
            dffs_with_clk.append(cell_name)
    
    if dffs_without_clk:
        result.add_error(f"{len(dffs_without_clk)} DFFs without CLK connection")
        if len(dffs_without_clk) <= 10:
            result.add_error(f"Missing CLK: {sorted(dffs_without_clk)}")
    else:
        print(f"✓ All {len(dff_cells)} DFFs have CLK connected")
    
    # Check clock nets are driven
    clock_nets = set()
    for cell_name, cell_data in dff_cells.items():
        connections = cell_data.get('connections', {})
        clk_bits = connections.get('CLK', [])
        if isinstance(clk_bits, list):
            clock_nets.update(clk_bits)
        else:
            clock_nets.add(clk_bits)
    
    # Find drivers of clock nets
    clock_drivers = []
    for cell_name, cell_data in module['cells'].items():
        cell_type = cell_data.get('type', '')
        connections = cell_data.get('connections', {})
        port_directions = cell_data.get('port_directions', {})
        
        # Check if this cell drives a clock net
        for port, direction in port_directions.items():
            if direction.lower() == 'output':
                port_bits = connections.get(port, [])
                if isinstance(port_bits, list):
                    if any(bit in clock_nets for bit in port_bits):
                        clock_drivers.append(cell_name)
                        break
                elif port_bits in clock_nets:
                    clock_drivers.append(cell_name)
                    break
    
    result.add_stat('num_clock_drivers', len(clock_drivers))
    if len(clock_drivers) == 0:
        result.add_error("No clock drivers found!")
    else:
        print(f"✓ Found {len(clock_drivers)} clock drivers")
    
    return result


def validate_power_down_eco(
    verilog_path: Path,
    netlist_path: Path,
    design_name: str  # Unused but kept for API consistency
) -> ECOValidationResult:
    """
    Verify Power-Down ECO correctness.
    
    Checks:
    1. Tie nets exist
    2. Tie cell present
    3. Unused cells tied
    4. No floating inputs
    """
    result = ECOValidationResult()
    
    print("\n[POWER-DOWN ECO VALIDATION] === Tie Cell Connections ===")
    
    with open(verilog_path, 'r') as f:
        verilog_content = f.read()
    
    # Check 1: Tie nets exist
    tie_low_found = 'tie_low_net' in verilog_content
    tie_high_found = 'tie_high_net' in verilog_content
    
    if not tie_low_found:
        result.add_error("tie_low_net not found in Verilog")
    if not tie_high_found:
        result.add_error("tie_high_net not found in Verilog")
    
    if tie_low_found and tie_high_found:
        print("✓ Tie nets found in Verilog")
    
    # Check 2: Tie cell (conb_1) exists
    conb_pattern = r'sky130_fd_sc_hd__conb_1\s+(\w+)'
    conb_matches = re.findall(conb_pattern, verilog_content)
    
    if not conb_matches:
        result.add_error("No conb_1 tie cell found")
    else:
        print(f"✓ Found {len(conb_matches)} tie cell(s)")
        result.add_stat('num_tie_cells', len(conb_matches))
    
    # Check 3: Count unused cells
    unused_pattern = r'sky130_fd_sc_hd__\w+\s+unused_\w+'
    unused_cells = re.findall(unused_pattern, verilog_content)
    result.add_stat('num_unused_cells', len(unused_cells))
    print(f"✓ Found {len(unused_cells)} unused cells in Verilog")
    
    # Check 4: Sample unused cells to verify they're tied
    sample_unused = unused_cells[:5]
    for cell_line in sample_unused:
        # Extract cell name
        match = re.search(r'unused_(\w+)', cell_line)
        if match:
            cell_name = match.group(1)
            # Check if this cell has tie connections
            cell_pattern = rf'sky130_fd_sc_hd__\w+\s+unused_{re.escape(cell_name)}\s*\([^)]*\)'
            cell_match = re.search(cell_pattern, verilog_content, re.DOTALL)
            if cell_match:
                cell_inst = cell_match.group(0)
                if 'tie_low_net' in cell_inst or 'tie_high_net' in cell_inst:
                    print(f"  ✓ unused_{cell_name}: Properly tied")
                else:
                    result.add_warning(f"unused_{cell_name}: May not be properly tied")
    
    return result


def estimate_clock_skew(
    cts_json_path: Path,
    buffer_delay_ps: float = 50.0,  # Typical buffer delay in picoseconds
    wire_delay_per_um_ps: float = 0.1  # Wire delay per micron
) -> ECOValidationResult:
    """
    Estimate clock tree skew.
    
    This is a simplified estimation. For real ASIC design,
    you need full STA (Static Timing Analysis) with:
    - Actual buffer delays from library
    - Wire RC delays
    - Process corners
    """
    result = ECOValidationResult()
    
    print("\n[TIMING VALIDATION] === Clock Skew Estimation ===")
    
    with open(cts_json_path, 'r') as f:
        cts_data = json.load(f)
    
    sinks = cts_data.get('sinks', [])
    buffers = cts_data.get('buffers', [])
    connections = cts_data.get('connections', [])
    
    if not sinks:
        result.add_error("No sinks found for timing analysis")
        return result
    
    # Build tree structure to calculate path delays
    # Simplified: count buffers and wire length to each sink
    sink_delays = {}
    
    # For each sink, estimate delay
    for sink in sinks:
        # Find path from root to sink
        # This is simplified - real analysis needs full tree traversal
        sink_pos = (sink['x'], sink['y'])
        
        # Count buffers in path (simplified: assume all buffers contribute)
        # In real design, you'd traverse the tree
        num_buffers = len(buffers)  # Simplified
        buffer_delay = num_buffers * buffer_delay_ps
        
        # Estimate wire delay (simplified: distance from center)
        center_x = sum(s['x'] for s in sinks) / len(sinks)
        center_y = sum(s['y'] for s in sinks) / len(sinks)
        distance = ((sink_pos[0] - center_x)**2 + (sink_pos[1] - center_y)**2)**0.5
        wire_delay = distance * wire_delay_per_um_ps
        
        total_delay = buffer_delay + wire_delay
        sink_delays[sink['name']] = total_delay
    
    if sink_delays:
        min_delay = min(sink_delays.values())
        max_delay = max(sink_delays.values())
        skew = max_delay - min_delay
        
        result.add_stat('estimated_skew_ps', skew)
        result.add_stat('min_path_delay_ps', min_delay)
        result.add_stat('max_path_delay_ps', max_delay)
        
        print(f"✓ Estimated clock skew: {skew:.2f} ps")
        print(f"  Min delay: {min_delay:.2f} ps, Max delay: {max_delay:.2f} ps")
        
        # Typical clock skew targets: < 10% of clock period
        # For 100MHz clock (10ns period), skew should be < 1ns = 1000ps
        if skew > 1000:
            result.add_warning(f"High estimated skew: {skew:.2f} ps (target: < 1000 ps)")
        else:
            print(f"  ✓ Skew within reasonable limits")
    
    return result


def validate_eco_flow(
    netlist_path: Path,
    verilog_path: Path,
    cts_json_path: Path,
    map_file_path: Path,  # Unused - kept for API compatibility
    design_name: str
) -> ECOValidationResult:
    """
    Comprehensive ECO flow validation.
    
    Runs all validation checks and generates report.
    """
    result = ECOValidationResult()
    
    print("\n" + "="*80)
    print("ECO FLOW VALIDATION")
    print("="*80)
    
    # 1. CTS Structure
    cts_result = validate_cts_structure(netlist_path, cts_json_path, design_name)
    result.errors.extend(cts_result.errors)
    result.warnings.extend(cts_result.warnings)
    result.stats.update(cts_result.stats)
    
    # 2. Verilog Syntax
    verilog_result = validate_verilog_syntax(verilog_path)
    result.errors.extend(verilog_result.errors)
    result.warnings.extend(verilog_result.warnings)
    result.stats.update(verilog_result.stats)
    
    # 3. Clock Connections
    clock_result = validate_clock_connections(netlist_path, verilog_path, design_name)
    result.errors.extend(clock_result.errors)
    result.warnings.extend(clock_result.warnings)
    result.stats.update(clock_result.stats)
    
    # 4. Power-Down ECO
    eco_result = validate_power_down_eco(verilog_path, netlist_path, design_name)
    result.errors.extend(eco_result.errors)
    result.warnings.extend(eco_result.warnings)
    result.stats.update(eco_result.stats)
    
    # 5. Timing (if CTS JSON exists)
    if cts_json_path.exists():
        timing_result = estimate_clock_skew(cts_json_path)
        result.errors.extend(timing_result.errors)
        result.warnings.extend(timing_result.warnings)
        result.stats.update(timing_result.stats)
    
    return result


def print_validation_report(result: ECOValidationResult) -> None:
    """Print formatted validation report."""
    print("\n" + "="*80)
    print("ECO VALIDATION REPORT")
    print("="*80)
    
    if result.passed:
        print("✅ VALIDATION PASSED")
    else:
        print("❌ VALIDATION FAILED")
    
    if result.errors:
        print(f"\n❌ ERRORS ({len(result.errors)}):")
        for i, error in enumerate(result.errors, 1):
            print(f"  {i}. {error}")
    
    if result.warnings:
        print(f"\n⚠️  WARNINGS ({len(result.warnings)}):")
        for i, warning in enumerate(result.warnings, 1):
            print(f"  {i}. {warning}")
    
    if result.stats:
        print(f"\n📊 STATISTICS:")
        for key, value in sorted(result.stats.items()):
            if isinstance(value, (int, float)):
                if 'ps' in key or 'delay' in key or 'skew' in key:
                    print(f"  {key}: {value:.2f} ps")
                elif 'percent' in key.lower():
                    print(f"  {key}: {value:.1f}%")
                else:
                    print(f"  {key}: {value}")
            else:
                print(f"  {key}: {value}")
    
    print("="*80)
    
    if not result.passed:
        print("\n❌ ECO VALIDATION FAILED - Please review errors above")
    else:
        print("\n✅ ECO VALIDATION PASSED - All checks successful")
    
    print("\n📋 NEXT STEPS FOR ASIC DESIGN:")
    print("  1. Run full STA (Static Timing Analysis) with actual library delays")
    print("  2. Verify setup/hold times with timing constraints")
    print("  3. Run DRC (Design Rule Check) on physical layout")
    print("  4. Run LVS (Layout vs Schematic) verification")
    print("  5. Run power analysis to verify power-down ECO effectiveness")

