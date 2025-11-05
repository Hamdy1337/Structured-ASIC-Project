# Understanding the Netlist Parser

## What We're Parsing

The `[design_name]_mapped.json` file is a Yosys-generated netlist. It looks like this:

```json
{
  "modules": {
    "sasic_top": {
      "ports": {
        "clk": {"direction": "input", "bits": [2]},
        "rst_n": {"direction": "input", "bits": [3]}
      },
      "cells": {
        "$abc$123": {
          "type": "sky130_fd_sc_hd__nand2_1",
          "connections": {
            "A": [24],    // Port A connected to net 24
            "B": [25],    // Port B connected to net 25
            "Y": [124]    // Port Y (output) connected to net 124
          }
        },
        "$abc$456": {
          "type": "sky130_fd_sc_hd__dff_1",
          "connections": {
            "D": [124],   // Connected to net 124 (from nand2 output)
            "Q": [126]
          }
        }
      }
    }
  }
}
```

## What We Need to Extract

### 1. **logical_db** (For Validation)
Purpose: Group cells by type so we can check "Do we have enough NAND2 cells in the fabric?"

Structure:
```python
logical_db = {
    "sky130_fd_sc_hd__nand2_1": ["$abc$123", "$abc$789", ...],  # All NAND2 instances
    "sky130_fd_sc_hd__dff_1": ["$abc$456", ...],                 # All DFF instances
    ...
}
```

**How to build it:**
1. Loop through all cells
2. For each cell, get its `type`
3. Add the cell's `name` to the list for that type

---

### 2. **netlist_graph** (For Placement)
Purpose: Understand which cells are connected together so the placer can place connected cells close to each other.

Structure:
```python
netlist_graph = {
    'cells': {
        "$abc$123": {
            "A": [24],    # Port A connected to net 24
            "B": [25],    # Port B connected to net 25
            "Y": [124]    # Port Y connected to net 124
        },
        "$abc$456": {
            "D": [124],   # Port D connected to net 124 (same net as $abc$123's Y!)
            "Q": [126]
        }
    },
    'net_to_cells': {
        24: ["$abc$123"],        # Net 24 is only used by cell $abc$123
        124: ["$abc$123", "$abc$456"]  # Net 124 connects these two cells!
    }
}
```

**How to build it:**
1. For each cell, store its connections (port → net)
2. Build reverse mapping: for each net, which cells use it
3. This tells us: "Net 124 connects cell $abc$123 to cell $abc$456"

---

## Why This Matters

### For Validation (Phase 1):
- Compare `len(logical_db["sky130_fd_sc_hd__nand2_1"])` with available NAND2 slots in fabric
- If design needs 5000 NAND2s but fabric only has 4000 → ERROR!

### For Placement (Phase 2):
- If cell A and cell B share a net, they should be placed close together
- The placer uses `net_to_cells` to find which cells are connected
- Then calculates wirelength (HPWL) to minimize total distance

---

## Step-by-Step Process

1. **Load JSON** → Get the file into Python
2. **Find Top Module** → Usually "sasic_top"
3. **Extract Cells** → Get all cell instances
4. **Group by Type** → Create logical_db
5. **Extract Connections** → Get port-to-net mappings
6. **Build Graph** → Create net_to_cells mapping
7. **Return Both** → logical_db + netlist_graph

---

## File Comparison

- **`netlist_parser_simple.py`**: Educational version with step-by-step comments
- **`netlist_parser.py`**: Full-featured version with error handling and helper methods

Start with the simple version to understand the concepts, then use the full version for your project!

