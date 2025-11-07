"""fabric_db.py
    Merges fabric_cells.yaml db and fabric.yaml db into a dataframe.
"""
import pandas as pd
from fabric_parser import parse_fabric_file
from fabric_cells_parser import parse_fabric_cells_file

def get_fabric_db(file_path_fabric: str, file_path_fabric_cells: str) -> pd.DataFrame:
    _, df_fabric = parse_fabric_file(file_path_fabric)
    _, df_fabric_cells = parse_fabric_cells_file(file_path_fabric_cells)

    #Rename columns
    df_fabric.rename(columns={
        "template_name": "cell_name"
    }, inplace=True)

    df_fabric_cells["cell_name"] = df_fabric_cells["cell_name"].str.split("__").str[1]
    df_merged = pd.merge(
        df_fabric_cells,
        df_fabric,
        how='left',
        left_on='cell_name',
        right_on='cell_name'
    )
    return df_merged

#  Ensure the DataFrame has the expected columns
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python fabric_db.py <fabric_file_path> <fabric_cells_file_path>")
        sys.exit(1)

    fabric_file_path = sys.argv[1]
    fabric_cells_file_path = sys.argv[2]

    df = get_fabric_db(fabric_file_path, fabric_cells_file_path)
    print(df.head(20))