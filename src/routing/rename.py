import argparse
import os
import sys
import re

# Add root directory to sys.path to allow importing src modules
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

try:
    from src.parsers.fabric_parser import parse_fabric_file_cached
except ImportError:
    # Fallback or error if not found (should be there)
    print("Warning: Could not import src.parsers.fabric_parser. Module renaming may fail.")
    parse_fabric_file_cached = None

# Keywords to preserve (not an exhaustive list, but covers structural Verilog)
KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "assign",
    "always", "begin", "end", "case", "endcase", "default", "if", "else",
    "parameter", "localparam", "generate", "endgenerate", "genvar", "for",
    "posedge", "negedge", "or", "and", "nand", "nor", "xor", "xnor", "not",
    "buf", "tran", "pullup", "pulldown", "primitive", "endprimitive",
    "table", "endtable", "specify", "endspecify", "initial"
}

def get_module_map(fabric_path):
    """Load macro map from fabric.yaml: template_name -> cell_type (sky130...)"""
    if not parse_fabric_file_cached:
        print("Warning: parse_fabric_file_cached not available. Module renaming skipped.")
        return {}
    
    print(f"Loading fabric from {fabric_path} for module renaming...")
    fabric, _ = parse_fabric_file_cached(fabric_path)
    if not fabric:
        print("Warning: Failed to load fabric. Module renaming skipped.")
        return {}
    
    # Use SAME logic as generate_def.py's get_macro_map_from_fabric
    macro_map = {}
    tile_def = getattr(fabric, 'tile_definition', {}) or {}
    for cell in tile_def.get('cells', []) or []:
        try:
            template_name = cell['template_name']
            cell_type = cell['cell_type']
            macro_map[str(template_name)] = str(cell_type)
        except Exception:
            continue
            
    print(f"Loaded {len(macro_map)} module mappings.")
    if len(macro_map) > 0:
        # Print a few examples for verification
        examples = list(macro_map.items())[:3]
        print(f"  Examples: {examples}")
    return macro_map

def sanitize_token(match):
    token = match.group(0)
    if token in KEYWORDS:
        return token
    # Sanitize special characters that are invalid in Verilog identifiers
    # Note: [ and ] in signal names cause OpenROAD to misparse them as bus declarations
    return token.replace("$", "_").replace(".", "_").replace(":", "_").replace("\\", "_").replace("[", "_").replace("]", "_")

def rename_instances(design_name, fabric_path=None, suffix=""):
    # Paths (suffix allows distinguishing RL from SA outputs, e.g., suffix="_rl")
    build_dir = os.path.join("build", design_name)
    verilog_path = os.path.join(build_dir, f"{design_name}{suffix}_final.v")
    map_path = os.path.join(build_dir, f"{design_name}{suffix}.map")
    output_path = os.path.join(build_dir, f"{design_name}{suffix}_renamed.v")

    print(f"Processing design: {design_name}")
    print(f"Reading map: {map_path}")
    print(f"Reading verilog: {verilog_path}")
    
    # Load Module Map (Template -> Physical)
    module_map = {}
    if fabric_path:
        module_map = get_module_map(fabric_path)

    # Load Instance Map (Logical Inst -> TXY_Inst)
    inst_map = {}
    if os.path.exists(map_path):
        with open(map_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                parts = line.split()
                if len(parts) == 2:
                    logical, physical = parts
                    clean_logical = logical.replace("$", "_").replace(".", "_").replace(":", "_").replace("\\", "_")
                    inst_map[clean_logical] = physical
        print(f"Loaded {len(inst_map)} instance mappings.")
    else:
        print(f"Warning: Map file {map_path} not found. Instance renaming skipped.")

    identifier_pattern = re.compile(r'(?<![\w\.])([a-zA-Z_\\$][\w\.:\\$]*)')
    # Capture: (Indent+ModuleType+Space)(InstanceName)(Space+Paren)
    instance_pattern = re.compile(r"^(\s*[\w]+\s+)([\w]+)(\s*\()", re.MULTILINE)

    replaced_inst_count = 0
    replaced_mod_count = 0
    
    with open(verilog_path, 'r') as fin, open(output_path, 'w') as fout:
        for line in fin:
            if not line.strip():
                fout.write(line)
                continue

            # 1. Sanitize entire line (token replacement)
            sanitized_line = identifier_pattern.sub(sanitize_token, line)
            
            # 1b. Global bracket replacement for signal names like _abc[0:0]
            # These appear in wire declarations and can't be captured by identifier pattern
            # We need to replace [X] or [X:Y] patterns that are part of signal names (not bus declarations)
            # Pattern: identifier followed by [digits] or [digits:digits] where identifier has underscore prefix
            sanitized_line = re.sub(r'(_\w+)\[(\d+):(\d+)\]', r'\1_\2_\3_', sanitized_line)
            sanitized_line = re.sub(r'(_\w+)\[(\d+)\]', r'\1_\2_', sanitized_line)
            
            # 2. Rename Top Module text
            # Note: Verilog module names cannot start with a digit, so prepend 'm_' if needed
            if "module sasic_top" in sanitized_line:
                verilog_safe_name = design_name if not design_name[0].isdigit() else f"m_{design_name}"
                sanitized_line = sanitized_line.replace("module sasic_top", f"module {verilog_safe_name}")

            # 3. Rename Instance AND Module Type
            match = instance_pattern.match(sanitized_line)
            if match:
                prefix_group = match.group(1) # "  R0_BUF_0 "
                inst_name = match.group(2)    # "inst_1"
                paren_suffix = match.group(3)       # " ("
                
                # A. Rename Module Type
                # Extract clean module name from prefix (strip spaces)
                old_mod_type = prefix_group.strip()
                new_mod_type = module_map.get(old_mod_type, old_mod_type)
                
                if new_mod_type != old_mod_type:
                    # Reconstruct prefix: preserve indentation?
                    # prefix_group starts with spaces.
                    # We regex replace last word in prefix_group?
                    # Or just construct new prefix.
                    # Assuming prefix is "  Module "
                    prefix_indent = prefix_group[: -len(old_mod_type) - (1 if prefix_group.endswith(' ') else 0) ] 
                    # Easier: just split and rejoin? No, preserve indent.
                    # Use regex sub on prefix_group
                    new_prefix = prefix_group.replace(old_mod_type, new_mod_type)
                    replaced_mod_count += 1
                else:
                    new_prefix = prefix_group

                # B. Rename Instance Name
                if inst_name in inst_map:
                    new_inst_name = inst_map[inst_name]
                    replaced_inst_count += 1
                else:
                    new_inst_name = inst_name
                
                # Write new line
                new_line = f"{new_prefix}{new_inst_name}{paren_suffix}{sanitized_line[match.end():]}"
                fout.write(new_line)
            else:
                fout.write(sanitized_line)

    print(f"Renamed {replaced_inst_count} instances and {replaced_mod_count} modules.")
    print(f"Output: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("design_name", help="Name of the design (e.g. arith)")
    parser.add_argument("--fabric", help="Path to fabric.yaml for module renaming", default=None)
    parser.add_argument("--suffix", help="Suffix for file names (e.g. '_rl' for RL flow)", default="")
    
    args = parser.parse_args()
    rename_instances(args.design_name, args.fabric, args.suffix)
