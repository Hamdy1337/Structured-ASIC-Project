
import sys
import os

def merge_lefs(tlef_path, lef_path, output_path):
    print(f"Merging {tlef_path} and {lef_path} into {output_path}...")
    
    with open(tlef_path, 'r') as f:
        tlef_lines = f.readlines()
        
    with open(lef_path, 'r') as f:
        lef_lines = f.readlines()
        
    # TLEF: Keep everything EXCEPT the last line if it is "END LIBRARY"
    # Actually, ODB LEF parser might look for "END LIBRARY"
    # If we concatenate, the file looks like:
    # ...
    # END LIBRARY
    # VERSION 5.7 ;
    # ...
    # END LIBRARY
    
    # We want to remove the first "END LIBRARY" and the second file's Header.
    # Typical Header stops at the first PROPERTY definition or LAYER or MACRO.
    # But safe bet: just strip lines starting with "VERSION", "BUSBITCHARS", "DIVIDERCHAR" from the second file.
    # And strip "END LIBRARY" from first file.
    
    # Check TLEF trailer
    clean_tlef = []
    for line in tlef_lines:
        if line.strip().startswith("END LIBRARY"):
            continue
        clean_tlef.append(line)
        
    # Check LEF header
    clean_lef = []
    # Skip standard header lines in 2nd file to avoid re-definition warnings/errors
    header_keywords = ["VERSION", "BUSBITCHARS", "DIVIDERCHAR", "NAMESCASESENSITIVE"]
    
    for line in lef_lines:
        skip = False
        for kw in header_keywords:
            if line.strip().startswith(kw):
                skip = True
                break
        if not skip:
            clean_lef.append(line)
            
    # Combine
    with open(output_path, 'w') as f:
        f.writelines(clean_tlef)
        f.write("\n# Merged Standard Cells \n")
        f.writelines(clean_lef)
        
    print(f"Created merged LEF: {output_path}")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python merge_lef.py <tlef> <lef> <output>")
    else:
        merge_lefs(sys.argv[1], sys.argv[2], sys.argv[3])
