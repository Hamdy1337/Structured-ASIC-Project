import pandas as pd
import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.parsers.fabric_db import get_fabric_db

def analyze_fabric():
    fabric_path = "inputs/Platform/fabric.yaml"
    fabric_cells_path = "inputs/Platform/fabric_cells.yaml"
    print(f"Loading fabric from {fabric_path} and {fabric_cells_path}...")
    
    _, df = get_fabric_db(fabric_path, fabric_cells_path)
    
    if 'cell_type' not in df.columns:
        print("Error: 'cell_type' column not found in merged fabric DB")
        print("Columns:", df.columns)
        return

    print(f"Total sites: {len(df)}")
    print(f"Unique cell types: {df['cell_type'].unique()}")
    
    # Analyze X-coordinates by type
    for ctype in df['cell_type'].unique():
        sub = df[df['cell_type'] == ctype]
        xs = sorted(sub['cell_x'].unique())
        print(f"\nType: {ctype}")
        print(f"  Count: {len(sub)}")
        print(f"  Unique X cols: {len(xs)}")
        # Print ranges or gaps
        if len(xs) > 10:
            print(f"  X-coords (first 5): {xs[:5]}")
            print(f"  X-coords (last 5): {xs[-5:]}")
        else:
            print(f"  X-coords: {xs}")

if __name__ == "__main__":
    analyze_fabric()
