"""fabric_db.py
    Merges fabric_cells.yaml db and fabric.yaml db into a dataframe.
    
    python -m src.parsers.fabric_db
"""
import pandas as pd
from src.parsers.fabric_parser import parse_fabric_file, Fabric
from src.parsers.fabric_cells_parser import parse_fabric_cells_file

def get_fabric_db(file_path_fabric: str, file_path_fabric_cells: str) -> Tuple[Fabric, pd.DataFrame]:
    fabric, df_fabric = parse_fabric_file(file_path_fabric)
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
    return (fabric, df_merged)

#  Ensure the DataFrame has the expected columns
if __name__ == "__main__":
    fabric_file_path = "inputs/Platform/fabric.yaml"
    fabric_cells_file_path = "inputs/Platform/fabric_cells.yaml"

    _,df = get_fabric_db(fabric_file_path, fabric_cells_file_path)
    # write to CSV for inspection
    df.to_csv("fabric_db_output.csv", index=False)