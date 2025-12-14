
import argparse
import yaml
import os
import sys
import time

# Add the project root to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

def snap_to_track(value_um: float, start_um: float, pitch_um: float) -> float:
    """Snap a coordinate (in um) to the nearest routing track."""
    if pitch_um == 0:
        return value_um
    track_num = round((value_um - start_um) / pitch_um)
    snapped = start_um + (track_num * pitch_um)
    # Keep stable YAML-like rounding to avoid float noise.
    return round(snapped, 4)


def um_to_dbu(value_um: float, dbu_per_micron: int) -> int:
    return int(round(value_um * dbu_per_micron))


def fix_and_snap_pin(pin: dict, pin_placement: dict) -> tuple[int, int]:
    """Return (x_dbu, y_dbu) for a pin, snapped to track grids and nudged off boundaries.

    Notes:
    - met2 pins (south/north) are expected to align to met2 vertical tracks (X-grid).
    - met3 pins (east/west) are expected to align to met3 horizontal tracks (Y-grid),
      and also align X to met2 vertical grid to allow Via2 placement.
    """
    units = pin_placement.get('units', {})
    dbu_per_micron = int(units.get('dbu_per_micron', 1000))
    die = pin_placement.get('die', {})
    die_width = float(die.get('width_um', 0.0))
    die_height = float(die.get('height_um', 0.0))
    tracks = pin_placement.get('tracks', {})

    met2 = tracks.get('met2', {})
    met3 = tracks.get('met3', {})
    met2_start = float(met2.get('start_um', 0.23))
    met2_pitch = float(met2.get('step_um', 0.46))
    met3_start = float(met3.get('start_um', 0.34))
    met3_pitch = float(met3.get('step_um', 0.68))

    x = float(pin.get('x_um', 0.0))
    y = float(pin.get('y_um', 0.0))
    layer = str(pin.get('layer', ''))

    if layer == 'met2':
        # met2: snap X to met2 track grid
        x = snap_to_track(x, met2_start, met2_pitch)
        # Move boundary pins slightly inside so they have access points.
        if y == 0.0:
            y = met2_start
        elif die_height and y == die_height:
            y = snap_to_track(die_height - met2_start, met2_start, met2_pitch)
        # Also snap Y to met2 grid (matches make_tracks met2 in route.tcl)
        y = snap_to_track(y, met2_start, met2_pitch)

    elif layer == 'met3':
        # met3: snap Y to met3 track grid
        y = snap_to_track(y, met3_start, met3_pitch)
        # For via placement, ensure X aligns to met2 vertical grid.
        if x == 0.0:
            target_x = 0.5
            x = snap_to_track(target_x, met2_start, met2_pitch)
            if x < target_x:
                x = round(x + met2_pitch, 4)
        elif die_width and x == die_width:
            target_x = die_width - 0.5
            x = snap_to_track(target_x, met2_start, met2_pitch)
            if x > target_x:
                x = round(x - met2_pitch, 4)
        else:
            x = snap_to_track(x, met2_start, met2_pitch)

    else:
        # Fallback: just convert.
        pass

    return um_to_dbu(x, dbu_per_micron), um_to_dbu(y, dbu_per_micron)

from src.parsers.fabric_cells_parser import parse_fabric_cells_file

def parse_args():
    parser = argparse.ArgumentParser(description='Generate fixed DEF file for Structured ASIC')
    parser.add_argument('--design_name', required=True, help='Name of the design')
    parser.add_argument('--fabric_cells', required=True, help='Path to fabric_cells.yaml')
    parser.add_argument('--pins', required=True, help='Path to pins.yaml')
    parser.add_argument('--map', required=True, help='Path to placement .map file')
    parser.add_argument('--fabric_def', required=True, help='Path to fabric.yaml')
    parser.add_argument('--output', required=True, help='Path to output .def file')
    return parser.parse_args()

def load_yaml(path):
    print(f"Loading YAML: {path}")
    start = time.time()
    with open(path, 'r') as f:
        data = yaml.safe_load(f)
    print(f"Loaded {path} in {time.time()-start:.2f}s")
    return data

def parse_map_file(map_path):
    print(f"Parsing Map: {map_path}")
    # Map file format (per Project_Description.md):
    #   <logical_instance_name> <physical_slot_name>
    # For DEF generation we primarily need the set of *used* physical slots.
    used_physical = set()
    with open(map_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) >= 2:
                physical_name = parts[1]
                used_physical.add(physical_name)
    return used_physical

def get_macro_map(fabric_def):
    # Create a mapping from template_name (e.g. R0_TAP_0) to cell_type (e.g. sky130_fd_sc_hd__tapvpwrvgnd_1)
    macro_map = {}
    if 'tile_definition' in fabric_def and 'cells' in fabric_def['tile_definition']:
        for cell in fabric_def['tile_definition']['cells']:
            template_name = cell['template_name']
            cell_type = cell['cell_type']
            macro_map[template_name] = cell_type
    return macro_map

def generate_def(args):
    print("generate_def started.")
    
    # 1. Load small inputs
    fabric_def = load_yaml(args.fabric_def)
    macro_map = get_macro_map(fabric_def)
    
    pins_data = load_yaml(args.pins)

    dbu_per_micron = 1000
    if 'pin_placement' in pins_data:
        units = pins_data['pin_placement'].get('units', {})
        dbu_per_micron = int(units.get('dbu_per_micron', 1000))
    
    used_physical = parse_map_file(args.map)

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

            # IMPORTANT: DEF COMPONENT names must match instance names in the Verilog
            # that OpenROAD reads. The routing flow renames Verilog instances to
            # physical slot names, so we emit physical slot names here.
            def_comp_name = physical_name
            
            # Use exact coordinates from fabric_cells (already legalized)
            # Do NOT snap to 0.17um grid as it corrupts 0.46um pitch alignment (e.g. 5.46 -> 5.44)
            x_dbu = um_to_dbu(float(cell.x), dbu_per_micron)
            y_dbu = um_to_dbu(float(cell.y), dbu_per_micron)
            orient = cell.orient
            
            components.append(f"- {def_comp_name} {macro_name} + FIXED ( {x_dbu} {y_dbu} ) {orient} ;")

    print(f"Generated {len(components)} components.")

    # 4. Prepare Pins from pins.yaml
    pins_def_lines = []
    if 'pin_placement' in pins_data and 'pins' in pins_data['pin_placement']:
        pin_placement = pins_data['pin_placement']
        for pin in pins_data['pin_placement']['pins']:
            pin_name = pin['name']
            layer = pin['layer']
            # Snap pin coordinates to layer-specific track grids and move off boundaries.
            x_int, y_int = fix_and_snap_pin(pin, pin_placement)
            direction = pin['direction']
            
            # Keep pin box conservative vs pitch to avoid spacing/shorts.
            # Default half-size from prior fix: 0.34um total width.
            half_size = 170
            
            pins_def_lines.append(f"- {pin_name} + NET {pin_name}")
            pins_def_lines.append(f"  + DIRECTION {direction}")
            pins_def_lines.append(f"  + USE SIGNAL")
            pins_def_lines.append(f"  + PORT")
            pins_def_lines.append(f"    + LAYER {layer} ( -{half_size} -{half_size} ) ( {half_size} {half_size} )")
            pins_def_lines.append(f"    + FIXED ( {x_int} {y_int} ) N")
            pins_def_lines.append(f"  ;")

    # 5. Write DEF (single pass)
    print(f"Writing DEF to {args.output}...")
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w') as f:
        f.write("VERSION 5.8 ;\n")
        f.write("DIVIDERCHAR \"/\" ;\n")
        f.write("BUSBITCHARS \"[]\" ;\n")
        f.write(f"DESIGN {args.design_name} ;\n")
        # Keep DEF DBU consistent with pins.yaml (and LEF database units).
        dbu_per_micron = 1000
        if 'pin_placement' in pins_data:
            units = pins_data['pin_placement'].get('units', {})
            dbu_per_micron = int(units.get('dbu_per_micron', 1000))
        f.write(f"UNITS DISTANCE MICRONS {dbu_per_micron} ;\n")

        # DIEAREA from pins.yaml
        if 'pin_placement' in pins_data and 'die' in pins_data['pin_placement']:
            die = pins_data['pin_placement']['die']
            width_dbu = um_to_dbu(float(die['width_um']), dbu_per_micron)
            height_dbu = um_to_dbu(float(die['height_um']), dbu_per_micron)
            f.write(f"DIEAREA ( 0 0 ) ( {width_dbu} {height_dbu} ) ;\n")
        else:
            print("Warning: DIEAREA not found in pins.yaml, using default large area.")
            f.write("DIEAREA ( 0 0 ) ( 1003600 989200 ) ;\n")
             
        f.write(f"COMPONENTS {len(components)} ;\n")
        for comp in components:
            f.write(f"{comp}\n")
        f.write("END COMPONENTS\n")

        pin_count = 0
        if 'pin_placement' in pins_data and 'pins' in pins_data['pin_placement']:
            pin_count = len(pins_data['pin_placement']['pins'])

        f.write(f"PINS {pin_count} ;\n")
        if pins_def_lines:
            for line in pins_def_lines:
                f.write(f"{line}\n")
        f.write("END PINS\n")
        f.write("END DESIGN\n")
        
    print(f"Done. Output at {args.output}")

if __name__ == "__main__":
    args = parse_args()
    generate_def(args)
