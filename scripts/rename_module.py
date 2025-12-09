import sys

def rename_module(file_path):
    print(f"Processing {file_path}...")
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
        
        found = False
        with open(file_path, 'w') as f:
            for line in lines:
                if not found and "module sasic_top" in line:
                    new_line = line.replace("module sasic_top", "module z80")
                    f.write(new_line)
                    found = True
                    print("Renamed module sasic_top to z80")
                else:
                    f.write(line)
        
        if not found:
            print("Warning: 'module sasic_top' not found.")
        else:
            print("Done.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    rename_module("build/z80/z80_final_fixed.v")
