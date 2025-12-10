#!/usr/bin/env python3
"""
Fix pin coordinates that are exactly on die boundaries.
Moves them slightly inside (0.5um) to prevent OpenROAD routing errors.
"""

import yaml
import sys

def snap_to_track(value, start, pitch):
    """Snap a coordinate to the nearest track grid point."""
    # Find nearest track
    track_num = round((value - start) / pitch)
    snapped = start + (track_num * pitch)
    return round(snapped, 4)  # Round to avoid float precision issues

def fix_pin_coordinates(input_file, output_file):
    """
    Move pins from die boundaries and snap to layer-specific track grids.
    
    met2 (south/north): start=0.23μm, pitch=0.46μm
    met3 (east/west): start=0.34μm, pitch=0.68μm
    """
    with open(input_file, 'r') as f:
        data = yaml.safe_load(f)
    
    pin_placement = data['pin_placement']
    die = pin_placement['die']
    tracks = pin_placement['tracks']
    pins = pin_placement['pins']
    
    die_width = die['width_um']
    die_height = die['height_um']
    
    # Get track parameters
    met2_start = tracks['met2']['start_um']
    met2_pitch = tracks['met2']['step_um']
    met3_start = tracks['met3']['start_um']
    met3_pitch = tracks['met3']['step_um']
    
    fixed_count = 0
    
    for pin in pins:
        x = pin['x_um']
        y = pin['y_um']
        layer = pin['layer']
        orig_x, orig_y = x, y
        fixed = False
        
        # Fix pins on boundaries and snap to track grid
        # CRITICAL: met2 = VERTICAL tracks (X-axis), met3 = HORIZONTAL tracks (Y-axis)
        if layer == 'met2':  # South/North pins (on VERTICAL tracks)
            # Snap X to met2 VERTICAL track
            x = snap_to_track(x, met2_start, met2_pitch)
            
            # Fix Y boundary positions
            if y == 0.0:
                y = met2_start  # First track inside
                fixed = True
            elif y == die_height:
                y = die_height - met2_start
                y = snap_to_track(y, met2_start, met2_pitch)
                fixed = True
                
        elif layer == 'met3':  # East/West pins (on HORIZONTAL tracks)
            # Fix X boundary positions
            # CRITICAL: Snap X to MET2 grid (Vertical) to allow Via2 placement!
            # Via2 location = Intersection(Met2_Vertical_Track, Met3_Horizontal_Track)
            
            if x == 0.0:
                 # Start search slightly inside to clear boundary
                 target_x = 0.5 
                 x = snap_to_track(target_x, met2_start, met2_pitch)
                 if x < target_x: x += met2_pitch # Ensure we are inside
                 fixed = True
            elif x == die_width:
                 target_x = die_width - 0.5
                 x = snap_to_track(target_x, met2_start, met2_pitch)
                 if x > target_x: x -= met2_pitch # Ensure we are inside
                 fixed = True
            
            # Snap Y to met3 HORIZONTAL track matches pin layer
            y = snap_to_track(y, met3_start, met3_pitch)
        
        if fixed or x != orig_x or y != orig_y:
            pin['x_um'] = x
            pin['y_um'] = y
            if orig_x != x:
                print(f"Fixed {pin['name']}: x {orig_x} -> {x}")
            if orig_y != y:
                print(f"Fixed {pin['name']}: y {orig_y} -> {y}")
            fixed_count += 1
    
    # Write fixed data
    with open(output_file, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    
    print(f"\nFixed {fixed_count} pins")
    print(f"Output written to: {output_file}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fix_pin_boundaries.py <input_pins.yaml> [output_pins.yaml]")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file
    
    fix_pin_coordinates(input_file, output_file)
