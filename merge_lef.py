from pathlib import Path

# Use absolute paths or relative to CWD
base_dir = Path("c:/Users/Dell-/Desktop/AUC/AUC semester 7/Digital Design 2/DD2 Proj/Structred-ASIC-Project")
tlef_path = base_dir / 'inputs/Platform/sky130_fd_sc_hd.tlef'
lef_path = base_dir / 'inputs/Platform/sky130_fd_sc_hd.lef'
output_path = base_dir / 'inputs/Platform/sky130_fd_sc_hd.merged.lef'

print(f"Reading TLEF: {tlef_path}")
tlef_content = tlef_path.read_text(encoding='utf-8')

print(f"Reading LEF: {lef_path}")
lef_lines = lef_path.read_text(encoding='utf-8').splitlines()

# Find start of MACROs to skip header redundancy
start_idx = 0
found = False
for i, line in enumerate(lef_lines):
    if line.strip().startswith('MACRO'):
        start_idx = i
        found = True
        break

if found:
    print(f"Found first MACRO at line {start_idx + 1}. Appending from there.")
    merged_content = tlef_content + '\n\n' + '\n'.join(lef_lines[start_idx:])
else:
    print("Warning: No MACRO found in LEF. Appending entire file.")
    merged_content = tlef_content + '\n\n' + '\n'.join(lef_lines)

print(f"Writing merged LEF to: {output_path}")
output_path.write_text(merged_content, encoding='utf-8')
print("Done.")
