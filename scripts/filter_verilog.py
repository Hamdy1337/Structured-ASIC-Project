import re
import sys
import os

def filter_verilog(input_path, output_path):
    print(f"Filtering {input_path} -> {output_path}")
    
    with open(input_path, 'r') as f:
        content = f.read()
        
    # Pattern to match module instantiation:
    # ModuleName InstanceName ( ... );
    # We want to remove any instantiation where InstanceName starts with "unused_"
    # We assume standard structural Verilog format.
    
    # Regex explanation:
    # \b[\w]+ \s+       : Module name (word chars) followed by whitespace
    # (unused_[\w]+)    : Capture group 1: Instance name starting with unused_
    # \s* \(            : Optional whitespace and opening parenthesis
    # .*?               : Non-greedy match of connections (including newlines)
    # \);               : Closing parenthesis and semicolon
    
    # Note: re.DOTALL is needed so . matches newlines
    
    pattern = re.compile(r"^\s*[\w$:]+\s+(unused_[\w]+)\s*\(.*?\);\s*", re.DOTALL | re.MULTILINE)
    
    # Find all matches to log them (optional, maybe too many)
    matches = pattern.findall(content)
    print(f"Found {len(matches)} unused instances to remove.")
    
    # Substitute with empty string
    new_content = pattern.sub("", content)
    
    # Also need to clean up empty lines if any?
    # The regex includes the trailing newline? \s* covers it?
    
    with open(output_path, 'w') as f:
        f.write(new_content)
        
    print("Write complete.")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python filter_verilog.py <input_verilog> <output_verilog>")
        sys.exit(1)
        
    filter_verilog(sys.argv[1], sys.argv[2])
