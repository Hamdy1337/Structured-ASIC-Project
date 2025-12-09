import os
import sys
import re

# Keywords to preserve (not an exhaustive list, but covers structural Verilog)
KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "assign",
    "always", "begin", "end", "case", "endcase", "default", "if", "else",
    "parameter", "localparam", "generate", "endgenerate", "genvar", "for",
    "posedge", "negedge", "or", "and", "nand", "nor", "xor", "xnor", "not",
    "buf", "tran", "pullup", "pulldown", "primitive", "endprimitive",
    "table", "endtable", "specify", "endspecify", "initial"
}

def sanitize_token(match):
    token = match.group(0)
    # If it's a keyword, return as is
    if token in KEYWORDS:
        return token
    
    # Replace invalid characters with underscore
    # $ . : \
    new_token = token.replace("$", "_").replace(".", "_").replace(":", "_").replace("\\", "_")
    
    # Collapse multiple underscores if desired, but simple replacement is safer for uniqueness
    return new_token

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

    # 1. Load mapping and sanitize keys
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
            
            # Sanitize the LOGICAL key using the same logic we will apply to Verilog
            # We must use the regex logic to simulate how it would be tokenized/sanitized
            # But the key is a single token.
            # We just apply the replacement directly.
            clean_logical = logical.replace("$", "_").replace(".", "_").replace(":", "_").replace("\\", "_")
            mapping[clean_logical] = physical

    print(f"Loaded {len(mapping)} mappings (sanitized keys).")

    # Regex to find identifiers
    # Matches words starting with letter, _, $, or \
    # Followed by any number of word chars, ., :, $, \
    # We use a negative lookahead to avoid capturing ranges? No, ranges start with [
    # This regex is for individual tokens.
    identifier_pattern = re.compile(r'(?<![\w\.])([a-zA-Z_\\$][\w\.:\\$]*)')
    # (?<![\w\.]) is a lookbehind to ensure we match start of word.
    
    # Regex to capture instance declarations for renaming
    # This assumes the line has been sanitized already!
    # Pattern: spaces cell_type spaces instance_name spaces (
    instance_pattern = re.compile(r"^(\s*[\w]+\s+)([\w]+)(\s*\()", re.MULTILINE)

    replaced_count = 0
    
    with open(verilog_path, 'r') as fin, open(output_path, 'w') as fout:
        for line in fin:
            if not line.strip():
                fout.write(line)
                continue

            # 2. Sanitize the entire line first
            # This converts "wire $abc;" to "wire _abc;"
            # And "sky130.. $abc (..)" to "sky130.. _abc (..)"
            sanitized_line = identifier_pattern.sub(sanitize_token, line)
            
            # 3. Check for instance renaming on the sanitized line
            # Also rename top module if it is sasic_top
            if "module sasic_top" in sanitized_line:
                 sanitized_line = sanitized_line.replace("module sasic_top", f"module {design_name}")

            match = instance_pattern.match(sanitized_line)
            if match:
                prefix = match.group(1)
                lookup_name = match.group(2)
                suffix = match.group(3)
                
                if lookup_name in mapping:
                    physical_name = mapping[lookup_name]
                    # Reconstruct line with physical name
                    # Note: prefix and suffix come from sanitized_line, so they are clean.
                    # rest of line (pins) is also from sanitized_line.
                    new_line = f"{prefix}{physical_name}{suffix}{sanitized_line[match.end():]}"
                    fout.write(new_line)
                    replaced_count += 1
                else:
                    # Instance found but not in map (maybe standard cell or IO?)
                    fout.write(sanitized_line)
            else:
                # Not an instance declaration, just write sanitized line
                fout.write(sanitized_line)

    print(f"Renamed {replaced_count} instances.")
    print("Done.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python rename.py <design_name>")
        sys.exit(1)
    
    rename_instances(sys.argv[1])
