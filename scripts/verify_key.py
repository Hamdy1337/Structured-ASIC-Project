
map_file = r'c:\College Work\AUC Study Work\Digital Design 2\Project\Structred-ASIC-Project\build\6502\6502.map'
verilog_file = r'c:\College Work\AUC Study Work\Digital Design 2\Project\Structred-ASIC-Project\build\6502\6502_final.v'

with open(map_file, 'r') as f:
    for line in f:
        if line.startswith('#') or not line.strip(): continue
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0]
            print(f"Searching for key: {key}")
            break

found = False
with open(verilog_file, 'r') as f:
    for i, line in enumerate(f):
        if key in line:
            print(f"Found at line {i+1}: {line.strip()}")
            found = True
            # Print a few following lines to see context
            for _ in range(5):
                print(next(f, "").strip())
            break

if not found:
    print("Key not found in Verilog file.")
else:
    print("Keys found in Verilog file.")
