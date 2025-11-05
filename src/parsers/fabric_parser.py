"""fabric_parser
Utilities to parse a fabric YAML file into typed dataclasses/dicts and
into a flattened pandas.DataFrame for easy consumption.

Public API:
- parse_fabric_file(file_path: str) -> (Fabric, pd.DataFrame)
  Returns a Fabric dataclass instance and a DataFrame with tile cell rows.
"""

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Tuple, Optional

import yaml
import pandas as pd


@dataclass
class TileCell:
    template_name: str
    cell_type: str
    origin_x: int
    origin_y: int
    width_sites: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=lambda: dict())


@dataclass
class Fabric:
    fabric_info: Dict[str, Any]
    fabric_layout: Dict[str, int]
    cell_definitions: Dict[str, Dict[str, int]]
    tile_definition: Dict[str, Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict representation (useful for JSON/serialization)."""
        return asdict(self)


def _tile_cells_to_dataframe(tile_def: Dict[str, Any], cell_defs: Dict[str, Any]) -> pd.DataFrame:
    """Flatten tile_definition['cells'] into a DataFrame.

    Columns produced:
    - template_name, cell_type, origin_x, origin_y, width_sites, and all other keys
      present in the original cell dict under an `extra_` prefix.
    """
    records: List[Dict[str, Any]] = []
    for c in tile_def.get('cells', []):
        rec: Dict[str, Any] = {}
        rec['template_name'] = c.get('template_name')
        rec['cell_type'] = c.get('cell_type')

        origin : Dict[str, int] = c.get('origin_sites', {}) or {}
        # origin sites may use 'x'/'y' keys
        rec['origin_x'] = origin.get('x')
        rec['origin_y'] = origin.get('y')

        # look up width_sites from cell_definitions when available
        cell_type = rec['cell_type']
        width: int | None = None
        if cell_type in cell_defs:
            try:
                width = cell_defs[cell_type].get('width_sites')
            except Exception:
                width = None
        rec['width_sites'] = width

        # Capture any other keys into an `extra_` group to preserve data
        for k, v in c.items():
            if k in ('template_name', 'cell_type', 'origin_sites'):
                continue
            # flatten simple values; keep dicts/complex as-is
            rec[f'extra_{k}'] = v

        records.append(rec)

    if not records:
        return pd.DataFrame(columns=['template_name', 'cell_type', 'origin_x', 'origin_y', 'width_sites'])

    df = pd.DataFrame.from_records(records)  # type: ignore
    # Normalize numeric columns
    for col in ('origin_x', 'origin_y', 'width_sites'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64') # type: ignore

    return df


def parse_fabric_file(file_path: str) -> Tuple[Fabric, pd.DataFrame]:
    """Parse a Fabric YAML file and return a Fabric object and a flattened DataFrame.

    Args:
        file_path: path to the fabric YAML file.

    Returns:
        (Fabric, pd.DataFrame): dataclass with the parsed YAML plus a DataFrame
        of tile cells for easy indexing/analysis.
    """
    with open(file_path, 'r', encoding='utf-8') as file:
        fabric_data: Dict[str, Any] = yaml.safe_load(file) or {}

    fabric_info : Dict[str, Any] = fabric_data.get('fabric_info', {})
    fabric_layout : Dict[str, int] = fabric_data.get('fabric_layout', {})
    cell_definitions : Dict[str, Dict[str, int]] = fabric_data.get('cell_definitions', {})
    tile_definition : Dict[str, Dict[str, Any]] = fabric_data.get('tile_definition', {})

    fabric = Fabric(
        fabric_info=fabric_info,
        fabric_layout=fabric_layout,
        cell_definitions=cell_definitions,
        tile_definition=tile_definition,
    )

    df = _tile_cells_to_dataframe(tile_definition, cell_definitions)

    return fabric, df

# Test Usage (Successful)
if __name__ == "__main__":
    fabric_path: str = "inputs/Platform/fabric.yaml"
    fabric, df = parse_fabric_file(fabric_path)
    print(fabric)
    print(df)
