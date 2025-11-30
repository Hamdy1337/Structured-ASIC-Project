import pandas as pd
import numpy as np
import sys

def check_diff(file1, file2):
    print(f"Loading {file1}...")
    df1 = pd.read_csv(file1)
    print(f"Loading {file2}...")
    df2 = pd.read_csv(file2)
    
    # Ensure sorted by cell_name to align rows
    df1 = df1.sort_values("cell_name").reset_index(drop=True)
    df2 = df2.sort_values("cell_name").reset_index(drop=True)
    
    if len(df1) != len(df2):
        print(f"ERROR: Cell counts differ! {len(df1)} vs {len(df2)}")
        return

    # Check if cell names match
    if not df1["cell_name"].equals(df2["cell_name"]):
        print("ERROR: Cell names do not align!")
        return
        
    # Calculate displacement
    dx = df1["x_um"] - df2["x_um"]
    dy = df1["y_um"] - df2["y_um"]
    dist = np.sqrt(dx**2 + dy**2)
    
    moved_mask = dist > 0.0001
    num_moved = moved_mask.sum()
    
    print("-" * 30)
    print(f"Total cells: {len(df1)}")
    print(f"Cells moved: {num_moved} ({num_moved/len(df1)*100:.2f}%)")
    
    if num_moved > 0:
        avg_disp = dist[moved_mask].mean()
        max_disp = dist[moved_mask].max()
        print(f"Avg displacement (of moved): {avg_disp:.4f} um")
        print(f"Max displacement: {max_disp:.4f} um")
    else:
        print("No cells moved!")

if __name__ == "__main__":
    f1 = "build/6502/6502_greedy_sa_placement.csv"
    f2 = "build/6502/6502_ppo_refined_placement.csv"
    check_diff(f1, f2)
