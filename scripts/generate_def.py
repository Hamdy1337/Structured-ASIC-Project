
import argparse
import yaml
import os
import sys
import time

# Add the project root to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

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
    mapping = {}
    with open(map_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) >= 2:
                logical_name = parts[0]
                physical_name = parts[1]
                mapping[physical_name] = logical_name
    return mapping

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
    
    placement_map = parse_map_file(args.map)

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
            
            # Only include placed components
            if physical_name not in placement_map:
                continue
            
            comp_name = placement_map[physical_name]
            
            # Escape backslashes for DEF format
            # DEF uses '\' as escape, so literal backslash must be '\\'
            def_comp_name = comp_name.replace("\\", "\\\\")
            
            x_dbu = int(cell.x * 1000)
            y_dbu = int(cell.y * 1000)
            orient = cell.orient
            
            components.append(f"- {def_comp_name} {macro_name} + FIXED ( {x_dbu} {y_dbu} ) {orient} ;")

    print(f"Generated {len(components)} components.")

    # 4. Prepare Pins from pins.yaml
    pins_def_lines = []
    if 'pin_placement' in pins_data and 'pins' in pins_data['pin_placement']:
        for pin in pins_data['pin_placement']['pins']:
            pin_name = pin['name']
            layer = pin['layer']
            x_int = int(pin['x_um'] * 1000)
            y_int = int(pin['y_um'] * 1000)
            direction = pin['direction']
            
            # Create a simple pin shape (0.2um box)
            half_size = 100 
            
            pins_def_lines.append(f"- {pin_name} + NET {pin_name}")
            pins_def_lines.append(f"  + DIRECTION {direction}")
            pins_def_lines.append(f"  + USE SIGNAL")
            pins_def_lines.append(f"  + PORT")
            pins_def_lines.append(f"    + LAYER {layer} ( -{half_size} -{half_size} ) ( {half_size} {half_size} )")
            pins_def_lines.append(f"    + FIXED ( {x_int} {y_int} ) N")
            pins_def_lines.append(f"  ;")

    # 5. Write DEF
    print(f"Writing DEF to {args.output}...")
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    
    with open(args.output, 'w') as f:
        f.write("VERSION 5.8 ;\n")
        f.write("DIVIDERCHAR \"/\" ;\n")
        f.write("BUSBITCHARS \"[]\" ;\n")
        f.write(f"DESIGN {args.design_name} ;\n")
        f.write("UNITS DISTANCE MICRONS 1000 ;\n")
        
        # DIEAREA from pins.yaml
        if 'pin_placement' in pins_data and 'die' in pins_data['pin_placement']:
            die = pins_data['pin_placement']['die']
            width_dbu = int(die['width_um'] * 1000)
            height_dbu = int(die['height_um'] * 1000)
            f.write(f"DIEAREA ( 0 0 ) ( {width_dbu} {height_dbu} ) ;\n")
        else:
            # Fallback (based on fabric.yaml if needed, but risky if fabric is small)
            # Default to huge if unknown
            print("Warning: DIEAREA not found in pins.yaml, using default large area.")
            f.write("DIEAREA ( 0 0 ) ( 1003600 989200 ) ;\n") 
        
        f.write(f"COMPONENTS {len(components)} ;\n")
        for comp in components:
            f.write(f"{comp}\n")
        f.write("END COMPONENTS\n")
        
        f.write(f"PINS {int(len(pins_def_lines)/7)} ;\n") # Approx count check? No, count loop
        # Count actual pins (each pin is 7 lines in my format above)
        # Better: store pins in list of strings
    
    # Refactoring pin write loop to be cleaner
    with open(args.output, 'a') as f:
        # PINS header was not written above?
        # Re-write PINS section properly
        pass 
        
    # Re-writing the write section to be correct
    with open(args.output, 'w') as f:
        f.write("VERSION 5.8 ;\n")
        f.write("DIVIDERCHAR \"/\" ;\n")
        f.write("BUSBITCHARS \"[]\" ;\n")
        f.write(f"DESIGN {args.design_name} ;\n")
        f.write("UNITS DISTANCE MICRONS 1000 ;\n")
        
        if 'pin_placement' in pins_data and 'die' in pins_data['pin_placement']:
            die = pins_data['pin_placement']['die']
            width_dbu = int(die['width_um'] * 1000)
            height_dbu = int(die['height_um'] * 1000)
            f.write(f"DIEAREA ( 0 0 ) ( {width_dbu} {height_dbu} ) ;\n")
        else:
             f.write("DIEAREA ( 0 0 ) ( 1003600 989200 ) ;\n")
             
        f.write(f"COMPONENTS {len(components)} ;\n")
        for comp in components:
            f.write(f"{comp}\n")
        f.write("END COMPONENTS\n")
        
        # Count pins
        pin_count = 0
        if 'pin_placement' in pins_data and 'pins' in pins_data['pin_placement']:
            pin_count = len(pins_data['pin_placement']['pins'])
            
        f.write(f"PINS {pin_count} ;\n")
        if pin_count > 0:
            for pin in pins_data['pin_placement']['pins']:
                pin_name = pin['name']
                layer = pin['layer']
                x_int = int(pin['x_um'] * 1000)
                y_int = int(pin['y_um'] * 1000)
                direction = pin['direction']
                half_size = 100
                f.write(f"- {pin_name} + NET {pin_name}\n")
                f.write(f"  + DIRECTION {direction}\n")
                f.write(f"  + USE SIGNAL\n")
                f.write(f"  + PORT\n")
                f.write(f"    + LAYER {layer} ( -{half_size} -{half_size} ) ( {half_size} {half_size} )\n")
                f.write(f"    + FIXED ( {x_int} {y_int} ) N\n")
                f.write(f"  ;\n")
        f.write("END PINS\n")
        f.write("END DESIGN\n")
        
    print(f"Done. Output at {args.output}")

if __name__ == "__main__":
    args = parse_args()
    generate_def(args)
