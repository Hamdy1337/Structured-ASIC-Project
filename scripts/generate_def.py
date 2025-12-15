from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, Set, Tuple

# Add the project root to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from src.parsers.fabric_cells_parser import parse_fabric_cells_file
from src.parsers.fabric_parser import parse_fabric_file_cached
from src.parsers.pins_parser import PinsMeta, load_and_validate_cached

def parse_args():
    parser = argparse.ArgumentParser(description='Generate fixed DEF file for Structured ASIC')
    parser.add_argument('--design_name', required=True, help='Name of the design')
    parser.add_argument('--fabric_cells', required=True, help='Path to fabric_cells.yaml')
    parser.add_argument('--pins', required=True, help='Path to pins.yaml')
    parser.add_argument('--map', required=True, help='Path to placement .map file')
    parser.add_argument('--fabric_def', required=True, help='Path to fabric.yaml')
    parser.add_argument('--output', required=True, help='Path to output .def file')
    return parser.parse_args()


def _um_to_dbu(value_um: float, dbu_per_micron: int) -> int:
    return int(round(value_um * dbu_per_micron))


def _snap_to_track(value_um: float, start_um: float, pitch_um: float) -> float:
    if pitch_um == 0:
        return value_um
    idx = round((value_um - start_um) / pitch_um)
    return round(start_um + idx * pitch_um, 4)


def parse_map_file(map_path: str) -> Set[str]:
    """Return set of used physical slot names.

    Map file format: <logical_instance_name> <physical_slot_name>
    """
    print(f"Parsing Map: {map_path}")
    used_physical: Set[str] = set()
    with open(map_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) >= 2:
                used_physical.add(parts[1])
    return used_physical


def get_macro_map_from_fabric(fabric) -> Dict[str, str]:
    macro_map: Dict[str, str] = {}
    tile_def = getattr(fabric, 'tile_definition', {}) or {}
    for cell in tile_def.get('cells', []) or []:
        try:
            template_name = cell['template_name']
            cell_type = cell['cell_type']
        except Exception:
            continue
        macro_map[str(template_name)] = str(cell_type)
    return macro_map


def _pin_fixed_location(pin_row, meta: PinsMeta) -> Tuple[int, int]:
    """Compute fixed (x_dbu, y_dbu) for DEF pins.

    pins.yaml places pins on die boundary. For OpenROAD accessibility we move them slightly inside
    and keep them snapped to routing track grids.
    """
    dbu = meta.units.dbu_per_micron
    die_w = meta.die.width_um
    die_h = meta.die.height_um

    layer = str(pin_row['layer'])
    x = float(pin_row['x_um'])
    y = float(pin_row['y_um'])

    # Track definitions in pins.yaml
    met2 = meta.tracks.get('met2')
    met3 = meta.tracks.get('met3')
    met2_start, met2_pitch = (met2.start_um, met2.step_um) if met2 else (0.23, 0.46)
    met3_start, met3_pitch = (met3.start_um, met3.step_um) if met3 else (0.34, 0.68)

    if layer == 'met2':
        # Snap X to met2 vertical grid
        x = _snap_to_track(x, met2_start, met2_pitch)

        # Nudge off boundary
        if y == 0.0:
            y = met2_start
        elif y == die_h:
            y = _snap_to_track(die_h - met2_start, met2_start, met2_pitch)

        # Keep Y aligned too
        y = _snap_to_track(y, met2_start, met2_pitch)

    elif layer == 'met3':
        # Snap Y to met3 horizontal grid
        y = _snap_to_track(y, met3_start, met3_pitch)

        # Ensure X aligns to met2 grid so Via2 intersections exist.
        if x == 0.0:
            target_x = 0.5
            x = _snap_to_track(target_x, met2_start, met2_pitch)
            if x < target_x:
                x = round(x + met2_pitch, 4)
        elif x == die_w:
            target_x = die_w - 0.5
            x = _snap_to_track(target_x, met2_start, met2_pitch)
            if x > target_x:
                x = round(x - met2_pitch, 4)
        else:
            x = _snap_to_track(x, met2_start, met2_pitch)

    return _um_to_dbu(x, dbu), _um_to_dbu(y, dbu)

def generate_def(args):
    print("generate_def started.")
    
    # 1. Load fabric + pins using parsers (with caching)
    fabric, _ = parse_fabric_file_cached(args.fabric_def)
    macro_map = get_macro_map_from_fabric(fabric)

    pins_df, pins_meta = load_and_validate_cached(args.pins)

    used_physical = parse_map_file(args.map)
    dbu_per_micron = pins_meta.units.dbu_per_micron

    # 2. Load fabric cells using OPTIMIZED parser
    print(f"Loading fabric cells from {args.fabric_cells}...")
    fabric_cells, _ = parse_fabric_cells_file(args.fabric_cells)
    
    components = []
    
    # 3. Process Tiles/Cells
    print(f"Processing {len(fabric_cells.tiles)} tiles...")
    
    for tile_name, tile in fabric_cells.tiles.items():
        for cell in tile.cells:
            physical_name = cell.name
            
            # physical_name format: T<X>Y<Y>__<TEMPLATE>
            try:
                parts = physical_name.split('__')
                if len(parts) < 2:
                    continue
                template_name = parts[1]
            except IndexError:
                continue

            macro_name = macro_map.get(template_name)
            if not macro_name:
                continue
            
            # Only include used/placed components (must exist in the Verilog netlist)
            if physical_name not in used_physical:
                continue

            # IMPORTANT: DEF component instance names must match the instance names
            # in the Verilog that OpenROAD reads. Our flow renames Verilog instances
            # to the physical slot name, so we use the physical slot name here.
            def_comp_name = physical_name
            
            # Do NOT snap cell locations; these are already legalized to the fabric grid.
            x_dbu = _um_to_dbu(float(cell.x), dbu_per_micron)
            y_dbu = _um_to_dbu(float(cell.y), dbu_per_micron)
            orient = cell.orient
            
            components.append(f"- {def_comp_name} {macro_name} + FIXED ( {x_dbu} {y_dbu} ) {orient} ;")

    print(f"Generated {len(components)} components.")

    # 4. Prepare Pins for DEF
    pins_def_lines = []
    half_size = 170  # conservative vs pitch to avoid spacing issues
    for _, pin in pins_df.iterrows():
        pin_name = str(pin['name'])
        layer = str(pin['layer'])
        direction = str(pin['direction'])
        x_int, y_int = _pin_fixed_location(pin, pins_meta)

        pins_def_lines.append(f"- {pin_name} + NET {pin_name}")
        pins_def_lines.append(f"  + DIRECTION {direction}")
        pins_def_lines.append(f"  + USE SIGNAL")
        pins_def_lines.append(f"  + PORT")
        pins_def_lines.append(f"    + LAYER {layer} ( -{half_size} -{half_size} ) ( {half_size} {half_size} )")
        pins_def_lines.append(f"    + FIXED ( {x_int} {y_int} ) N")
        pins_def_lines.append("  ;")

    # 5. Write DEF (single pass)
    print(f"Writing DEF to {args.output}...")
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write("VERSION 5.8 ;\n")
        f.write("DIVIDERCHAR \"/\" ;\n")
        f.write("BUSBITCHARS \"[]\" ;\n")
        f.write(f"DESIGN {args.design_name} ;\n")
        f.write(f"UNITS DISTANCE MICRONS {dbu_per_micron} ;\n")

        width_dbu = _um_to_dbu(pins_meta.die.width_um, dbu_per_micron)
        height_dbu = _um_to_dbu(pins_meta.die.height_um, dbu_per_micron)
        f.write(f"DIEAREA ( 0 0 ) ( {width_dbu} {height_dbu} ) ;\n")
             
        f.write(f"COMPONENTS {len(components)} ;\n")
        for comp in components:
            f.write(f"{comp}\n")
        f.write("END COMPONENTS\n")

        pin_count = len(pins_df)
        f.write(f"PINS {pin_count} ;\n")
        for line in pins_def_lines:
            f.write(f"{line}\n")
        f.write("END PINS\n")
        f.write("END DESIGN\n")
        
    print(f"Done. Output at {args.output}")

if __name__ == "__main__":
    args = parse_args()
    generate_def(args)
