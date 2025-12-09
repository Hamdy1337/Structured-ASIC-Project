"""
fabric_cells_parser.py
Utilities to parse a fabric_cells YAML file into typed dataclasses/dicts and
into a flattened pandas.DataFrame for easy consumption.

Public API:
- parse_fabric_cells_file(file_path: str) -> (FabricCells, pd.DataFrame)
    Returns a FabricCells dataclass instance and a DataFrame with tile cell rows.
"""

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Tuple
import yaml
import pandas as pd
import time

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
    cells: List[Cell] = field(default_factory=list)

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
    """Flatten tiles into a DataFrame."""
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
    
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=[
            "tile_name", "tile_x", "tile_y", "cell_name", "cell_orient", "cell_x", "cell_y"
        ])
        
    # Explicit casting to nullable float types
    cols = ['tile_x', 'tile_y', 'cell_x', 'cell_y']
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').astype(pd.Float32Dtype())
        
    return df

def parse_fabric_cells_file(file_path: str) -> Tuple[FabricCells, pd.DataFrame]:
    """
    Parse a fabric_cells YAML file into a FabricCells dataclass instance.
    OPTIMIZED: Uses line-by-line stream parsing to avoid memory/CPU bottleneck with large YAML files.
    """
    print(f"Parsing {file_path} using stream parser...")
    start_time = time.time()

    version = ""
    units = {}
    position_semantics = ""
    tiles = {}

    # State variables for parsing
    current_tile_name = None
    current_tile = None
    current_cell = None
    
    # We will simply manually parse the structure which is known to be:
    # fabric_cells_by_tile:
    #   version: ...
    #   units: ...
    #   tiles:
    #     TileName:
    #       x: ...
    #       y: ...
    #       cells:
    #         - name: ...
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            
            # Metadata (simplified regex-like matching)
            # Assumption: Metadata is at the top level or indented under root
            if ': ' in stripped:
                key, value = stripped.split(': ', 1)
                key = key.strip()
                value = value.strip()
                
                if key == 'version':
                    version = value
                elif key == 'position_semantics':
                    position_semantics = value
            
            # Tile Detection (e.g. "T0Y0:")
            # Tiles are keys under 'tiles:', so they end with ':' and are likely indented
            if stripped.endswith(':') and not stripped.startswith('-'):
                key = stripped[:-1].strip()
                if key in ['fabric_cells_by_tile', 'tiles', 'units', 'cells']:
                    continue
                # It's a tile name like T0Y0
                current_tile_name = key
                current_tile = Tile(x=0.0, y=0.0) # Defaults, will be filled
                tiles[current_tile_name] = current_tile
            
            # Tile Properties
            if current_tile and not current_cell:
                if stripped.startswith('x:'):
                    try:
                        current_tile.x = float(stripped.split(':', 1)[1].strip())
                    except ValueError: pass
                elif stripped.startswith('y:'):
                    try:
                        current_tile.y = float(stripped.split(':', 1)[1].strip())
                    except ValueError: pass
            
            # Cell Entry Start
            if stripped.startswith('- name:'):
                current_cell = {}
                name_val = stripped.split(':', 1)[1].strip()
                current_cell['name'] = name_val
            
            # Cell Properties
            if current_cell is not None:
                if stripped.startswith('orient:'):
                    current_cell['orient'] = stripped.split(':', 1)[1].strip()
                elif stripped.startswith('x:'):
                    try:
                        current_cell['x'] = float(stripped.split(':', 1)[1].strip())
                    except ValueError: pass
                elif stripped.startswith('y:'):
                    try:
                        current_cell['y'] = float(stripped.split(':', 1)[1].strip())
                        
                        # End of cell definition (y is usually last)
                        if 'name' in current_cell and current_tile:
                            new_cell = Cell(
                                name=current_cell['name'],
                                orient=current_cell.get('orient', 'N'),
                                x=current_cell.get('x', 0.0),
                                y=current_cell.get('y', 0.0)
                            )
                            current_tile.cells.append(new_cell)
                        current_cell = None 
                    except ValueError: pass

    print(f"Finished parsing {len(tiles)} tiles in {time.time()-start_time:.2f}s")
    
    fabric_cells = FabricCells(
        version=version,
        units=units,
        position_semantics=position_semantics,
        tiles=tiles
    )
    
    df = fabric_cells_to_dataframe(tiles)
    return fabric_cells, df

if __name__ == "__main__":
    # Test
    path = "inputs/Platform/fabric_cells.yaml"
    f, d = parse_fabric_cells_file(path)
    print(f"Loaded {len(f.tiles)} tiles.")
    print(d.head())