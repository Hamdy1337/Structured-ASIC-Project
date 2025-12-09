import sys

def parse_fabric_cells_stream(file_path):
    print(f"Reading {file_path}...")
    current_cell = {}
    count = 0
    with open(file_path, 'r') as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('- name:'):
                count += 1
                if count % 1000 == 0:
                    print(f"Parsed {count} cells...")
                if count > 5000:
                    break
    print(f"Finished parsing {count} cells.")

if __name__ == "__main__":
    parse_fabric_cells_stream("inputs/Platform/fabric_cells.yaml")
