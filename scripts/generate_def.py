import argparse
import yaml
import os
import sys

# Add the current directory to sys.path to import local modules if needed
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from lef_parser import LefParser

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
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def parse_map_file(map_path):
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
    print(f"Loading fabric cells from {args.fabric_cells}...")
    fabric_cells_data = load_yaml(args.fabric_cells)
    
    print(f"Loading pins from {args.pins}...")
    pins_data = load_yaml(args.pins)
    
    print(f"Loading placement map from {args.map}...")
    placement_map = parse_map_file(args.map)
    
    print(f"Loading fabric definition from {args.fabric_def}...")
    fabric_def = load_yaml(args.fabric_def)
    macro_map = get_macro_map(fabric_def)

    # 1. Prepare Components
    components = []
    
    # Iterate through all tiles and cells in fabric_cells.yaml
    if 'fabric_cells_by_tile' in fabric_cells_data and 'tiles' in fabric_cells_data['fabric_cells_by_tile']:
        tiles = fabric_cells_data['fabric_cells_by_tile']['tiles']
        for tile_name, tile_data in tiles.items():
            if 'cells' in tile_data:
                for cell in tile_data['cells']:
                    physical_name = cell['name']
                    x = cell['x']
                    y = cell['y']
                    orient = cell['orient']
                    
                    # Determine macro name
                    # Physical name format: T<X>Y<Y>__<TEMPLATE_NAME>
                    # We need to extract TEMPLATE_NAME to look up the macro
                    try:
                        # Split by double underscore to separate tile prefix from template name
                        parts = physical_name.split('__')
                        if len(parts) < 2:
                            print(f"Warning: Malformed cell name {physical_name}, skipping.")
                            continue
                        template_name = parts[1]
                    except IndexError:
                         print(f"Warning: Could not parse template name from {physical_name}, skipping.")
                         continue
                    
                    macro_name = macro_map.get(template_name)
                    if not macro_name:
                        print(f"Warning: No macro mapping found for template {template_name} (cell {physical_name}), skipping.")
                        continue

                    # Determine component name (Logical if used, Physical if unused)
                    comp_name = placement_map.get(physical_name, physical_name)
                    
                    # Convert coordinates to integer DBU (assuming 1000 DBU per micron based on pins.yaml)
                    # pins.yaml says dbu_per_micron: 1000. fabric_cells.yaml says coords: microns.
                    # DEF usually uses integer coordinates.
                    dbu = 1000
                    x_int = int(round(x * dbu))
                    y_int = int(round(y * dbu))
                    
                    components.append({
                        'name': comp_name,
                        'macro': macro_name,
                        'x': x_int,
                        'y': y_int,
                        'orient': orient
                    })

    # 2. Prepare Pins
    pins = []
    if 'pin_placement' in pins_data and 'pins' in pins_data['pin_placement']:
        for pin in pins_data['pin_placement']['pins']:
            pin_name = pin['name']
            # Only include pins that are relevant to the design? 
            # The prompt says "All PINS marked as + FIXED". 
            # Usually we only include pins that are actually in the design netlist.
            # However, for a "complete DEF containing all fixed placement information", 
            # and assuming this might be a top-level template or we want to lock down everything,
            # we will include all pins defined in pins.yaml.
            
            layer = pin['layer']
            x_um = pin['x_um']
            y_um = pin['y_um']
            direction = pin['direction']
            
            dbu = 1000
            x_int = int(round(x_um * dbu))
            y_int = int(round(y_um * dbu))
            
            pins.append({
                'name': pin_name,
                'net': pin_name, # Assuming net name equals pin name for top level
                'layer': layer,
                'x': x_int,
                'y': y_int,
                'direction': direction
            })

    # 3. Write DEF
    print(f"Writing DEF to {args.output}...")
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    
    with open(args.output, 'w') as f:
        f.write(f"VERSION 5.8 ;\n")
        f.write(f"DIVIDERCHAR \"/\" ;\n")
        f.write(f"BUSBITCHARS \"[]\" ;\n")
        f.write(f"DESIGN {args.design_name} ;\n")
        f.write(f"UNITS DISTANCE MICRONS 1000 ;\n")
        
        # DIEAREA
        if 'pin_placement' in pins_data and 'die' in pins_data['pin_placement']:
            die = pins_data['pin_placement']['die']
            width_um = die['width_um']
            height_um = die['height_um']
            width_dbu = int(round(width_um * 1000))
            height_dbu = int(round(height_um * 1000))
            f.write(f"DIEAREA ( 0 0 ) ( {width_dbu} {height_dbu} ) ;\n")
        
        # COMPONENTS
        f.write(f"COMPONENTS {len(components)} ;\n")
        for comp in components:
            # - <name> <macro> + FIXED ( <x> <y> ) <orient> ;
            f.write(f"- {comp['name']} {comp['macro']} + FIXED ( {comp['x']} {comp['y']} ) {comp['orient']} ;\n")
        f.write("END COMPONENTS\n")
        
        # PINS
        f.write(f"PINS {len(pins)} ;\n")
        for pin in pins:
            # - <name> + NET <net> + DIRECTION <dir> + USE SIGNAL + LAYER <layer> ( <x> <y> ) ( <x> <y> ) + FIXED ( <x> <y> ) <orient> ...
            # Simplified DEF PIN syntax:
            # - <pin_name> + NET <net_name> + DIRECTION <dir> + USE SIGNAL ...
            #   + PORT
            #     + LAYER <layer> ( 0 0 ) ( 0 0 ) # Rectangle relative to pin loc? No, usually LAYER defines the shape.
            #     + FIXED ( <x> <y> ) <orient> 
            
            # Let's use a standard point-based pin definition often used in simple DEFs
            # - pin_name + NET net_name + DIRECTION dir + USE SIGNAL + LAYER layer ( -half_width -half_height ) ( half_width half_height ) + FIXED ( x y ) N ;
            
            # Since we don't have pin sizes in pins.yaml (only x_um, y_um), we might need to assume a small box or just a point.
            # However, pins.yaml has 'units' and 'tracks' info, but not explicit pin geometry per pin.
            # Let's assume a small square via on the layer.
            # Actually, standard DEF requires a layer shape.
            # Let's check if we can infer size. 
            # pins.yaml has 'met2' step 0.46. Let's make a small box.
            
            # For now, let's create a minimal valid PIN entry.
            # We'll assume a small box centered on the point.
            half_size = 100 # 0.1um
            
            f.write(f"- {pin['name']} + NET {pin['net']}\n")
            f.write(f"  + DIRECTION {pin['direction']}\n")
            f.write(f"  + USE SIGNAL\n")
            f.write(f"  + PORT\n")
            # Creating a dummy shape for the pin since we only have center point
            f.write(f"    + LAYER {pin['layer']} ( -{half_size} -{half_size} ) ( {half_size} {half_size} )\n")
            f.write(f"    + FIXED ( {pin['x']} {pin['y']} ) N\n")
            f.write(f"  ;\n")
            
        f.write("END PINS\n")
        
        f.write("END DESIGN\n")

    print("Done.")

if __name__ == "__main__":
    args = parse_args()
    generate_def(args)
