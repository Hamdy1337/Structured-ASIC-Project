import sys
import os

print("Starting debug_import.py")
current_dir = os.path.dirname(os.path.abspath(__file__))
print(f"Current dir: {current_dir}")
project_root = os.path.join(current_dir, '..')
print(f"Project root: {project_root}")
sys.path.append(project_root)

try:
    from src.parsers.fabric_cells_parser import parse_fabric_cells_file
    print("Import successful!")
except ImportError as e:
    print(f"Import failed: {e}")
except Exception as e:
    print(f"An error occurred: {e}")
