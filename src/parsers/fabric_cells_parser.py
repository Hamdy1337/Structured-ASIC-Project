"""
fabric_cells_parser.py
Utilities to parse a fabric_cells YAML file into typed dataclasses/dicts and
into a flattened pandas.DataFrame for easy consumption.

Public API:
- parse_fabric_file(file_path: str) -> (Fabric, pd.DataFrame)
    Returns a Fabric dataclass instance and a DataFrame with tile cell rows.
"""

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Tuple

import yaml
import pandas as pd

@dataclass
class Cell:
    name : str
    orient: str
    x: float
    y: float

@dataclass
class Tile:
    x: float
    y: float
    cells: List[Cell] = field(default_factory=lambda: list())

@dataclass
class FabricCells:
    version: str
    units : Dict[str, str]
    position_semantics: str
    tiles: Dict[str, Tile]
    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict representation (useful for JSON/serialization)."""
        return asdict(self)

def fabric_cells_to_dataframe(tiles: Dict[str, Tile]) -> pd.DataFrame:
    """Flatten tiles into a DataFrame.
    
    Columns produced:
    - tile_name, tile_x, tile_y, cell_name, cell_orient, cell_x, cell_y
    """

    rows : List[Dict[str, Any]] = []
    for tile_name, tile in tiles.items():
        tile_x = tile.x
        tile_y = tile.y
        for cell in tile.cells:
            rows.append({
                "tile_name": tile_name,
                "tile_x": tile_x,
                "tile_y": tile_y,
                "cell_name": cell.name,
                "cell_orient": cell.orient,
                "cell_x": cell.x,
                "cell_y": cell.y,
            })
    # Normalize the numeric types
    df = pd.DataFrame(rows)
    # If there are no rows, return an empty DataFrame with expected columns
    if df.empty:
        return pd.DataFrame(columns=[
            "tile_name", "tile_x", "tile_y", "cell_name", "cell_orient", "cell_x", "cell_y"
        ])
    # Explicitly annotate intermediate Series so static type checkers know to_numeric
    # returns a Series before calling astype.
    tile_x_series: pd.Series = pd.to_numeric(df['tile_x'], errors='coerce') # type: ignore
    tile_y_series: pd.Series = pd.to_numeric(df['tile_y'], errors='coerce') # type: ignore
    cell_x_series: pd.Series = pd.to_numeric(df['cell_x'], errors='coerce') # type: ignore
    cell_y_series: pd.Series = pd.to_numeric(df['cell_y'], errors='coerce') # type: ignore
    df['tile_x'] = tile_x_series.astype(pd.Float32Dtype())
    df['tile_y'] = tile_y_series.astype(pd.Float32Dtype())
    df['cell_x'] = cell_x_series.astype(pd.Float32Dtype())
    df['cell_y'] = cell_y_series.astype(pd.Float32Dtype())
    return df


def parse_fabric_cells_file(file_path: str) -> Tuple[FabricCells, pd.DataFrame]:
    """Parse a fabric_cells YAML file into a FabricCells dataclass instance.

    Supports YAML shaped as either:
    - top-level keys (version, units, position_semantics, tiles), or
    - nested under a root key "fabric_cells_by_tile".
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}

    # Handle nested root key if present
    root: Dict[str, Any] = data.get('fabric_cells_by_tile', data)

    version: str = str(root.get('version', ''))
    units: Dict[str, str] = root.get('units', {})
    position_semantics: str = str(root.get('position_semantics', ''))
    raw_tiles: Dict[str, Dict[str, Any]] = root.get('tiles', {}) or {}

    # Convert raw tile data into Tile dataclass instances
    typed_tiles: Dict[str, Tile] = {}
    for tile_name, tile_data in raw_tiles.items():
        try:
            x_val = float(tile_data.get('x', 0.0))
        except Exception:
            x_val = 0.0
        try:
            y_val = float(tile_data.get('y', 0.0))
        except Exception:
            y_val = 0.0

        cells_list: List[Cell] = []
        cells_raw: List[Dict[str, Any]] = tile_data.get('cells', []) or []
        for cell_data in cells_raw:
            try:
                cx = float(cell_data.get('x', 0.0))
            except Exception:
                cx = 0.0
            try:
                cy = float(cell_data.get('y', 0.0))
            except Exception:
                cy = 0.0
            cells_list.append(Cell(
                name=str(cell_data.get('name', '')),
                orient=str(cell_data.get('orient', '')),
                x=cx,
                y=cy,
            ))

        typed_tiles[tile_name] = Tile(x=x_val, y=y_val, cells=cells_list)

    fabric_cells = FabricCells(
        version=version,
        units=units,
        position_semantics=position_semantics,
        tiles=typed_tiles,
    )

    df = fabric_cells_to_dataframe(typed_tiles)
    return fabric_cells, df

if __name__ == "__main__":
    fabric_cells_path: str = "inputs/Platform/fabric_cells.yaml"
    fabric_cells, df = parse_fabric_cells_file(fabric_cells_path)
    print(fabric_cells)
    print(df)