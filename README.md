# Structured ASIC Physical Design Flow

A complete automated physical design toolchain for Structured ASIC platforms, from netlist to timing signoff.

## Overview

This repository contains a full Place & Route (PnR) and Static Timing Analysis (STA) flow for Structured ASICs. Unlike traditional ASICs where cells can be placed anywhere, Structured ASICs use pre-fabricated wafers with fixed logic cell locations. Our flow solves the complex assignment problem of mapping logical gates to physical fabric slots, then routes and validates timing.

**Key Features:**
- Smart placement using Greedy + Simulated Annealing optimization
- Automated clock tree synthesis (CTS)
- Integration with OpenROAD for global/detailed routing
- Complete timing signoff with STA
- Rich visualizations at every stage

## Quick Start

```bash
# Clone the repository
git clone <repo-url>
cd structured-asic-flow

# Install dependencies
pip install -r requirements.txt

# Run the complete flow for a design
make all DESIGN=6502

# Results will be in build/6502/
```

## Architecture

The flow consists of five major stages:

### 1. Database & Validation
Parses platform files (fabric cells, pins, LEF, timing libraries) and design netlists. Validates that the design can fit on the available fabric by checking cell availability.

**Outputs:**
- Fabric utilization report
- Ground-truth layout visualization

### 2. Placement
Maps logical cells to physical fabric slots to minimize wirelength (HPWL).

**Algorithm:**
- **Greedy Initial Placement**: I/O-driven seed & grow algorithm for high-quality starting point
- **Simulated Annealing**: Refines placement using hybrid move set (local refinement + global exploration)

**Outputs:**
- Cell-to-slot mapping (`.map` file)
- Placement density heatmap
- Net length distribution histogram

### 3. Clock Tree Synthesis (CTS) & ECO
Builds a balanced clock tree using available buffers in the fabric. Generates engineering change order (ECO) to tie-off unused cells.

**Algorithm:**
- H-Tree/X-Tree geometric partitioning
- Recursive buffer insertion at geometric centers
- Power optimization by tying unused logic low

**Outputs:**
- Modified Verilog netlist with clock tree
- CTS tree visualization

### 4. Routing
Integrates with OpenROAD for global and detailed routing.

**Outputs:**
- Routed DEF file
- Parasitic extraction (SPEF)
- Congestion analysis

### 5. Static Timing Analysis
Post-route timing validation and performance analysis.

**Outputs:**
- Setup/hold timing reports
- Clock skew analysis
- Critical path visualization
- Slack histograms

## Usage

### Run Complete Flow
```bash
make all DESIGN=<design_name>
```

### Run Individual Stages
```bash
make validate DESIGN=6502    # Check if design fits on fabric
make place DESIGN=6502       # Run placement only
make cts DESIGN=6502         # Run clock tree synthesis
make route DESIGN=6502       # Run routing
make sta DESIGN=6502         # Run timing analysis
```

### Clean Build Files
```bash
make clean DESIGN=6502       # Clean specific design
make clean                   # Clean all designs
```

## Input Files

### Platform Files (Static)
- `fabric_cells.yaml` - Complete fabric database with all cell slots
- `pins.yaml` - I/O pin locations
- `sky130_hd.lef` - Physical abstracts for all cells
- `sky130_hd_timing.lib` - Timing models

### Design Files (Per Design)
- `<design>_mapped.json` - Logical netlist from Yosys
- `<design>.sdc` - Timing constraints (clock period, I/O delays)

## Output Files

All generated files are organized in `build/<design_name>/`:

```
build/6502/
├── 6502.map                    # Cell placement mapping
├── 6502_final.v                # Modified netlist with CTS
├── 6502_renamed.v              # Netlist with physical names
├── 6502_fixed.def              # DEF with fixed placements
├── 6502_routed.def             # Final routed design
├── 6502.spef                   # Parasitic extraction
├── 6502_setup.rpt              # Setup timing report
├── 6502_hold.rpt               # Hold timing report
├── visualizations/
│   ├── fabric_layout.png       # Ground-truth fabric
│   ├── placement_density.png   # Placement heatmap
│   ├── net_length.png          # Net length histogram
│   ├── cts_tree.png            # Clock tree visualization
│   ├── congestion.png          # Routing congestion
│   ├── critical_path.png       # Critical path overlay
│   └── slack_histogram.png     # Slack distribution
```

## Simulated Annealing Tuning

The placer uses Simulated Annealing with several tunable parameters:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `T_initial` | Initial temperature | 1000 |
| `alpha` | Cooling rate | 0.95 |
| `moves_per_temp` | Moves per temperature | 100 |
| `p_refine` | Probability of local refinement | 0.7 |
| `p_explore` | Probability of global exploration | 0.3 |
| `w_initial` | Initial exploration window size | 50% |

See `sa_knob_analysis.png` for experimental results showing the runtime vs. quality trade-off.

## Results

Performance across test designs:

| Design | Utilization | HPWL (μm) | WNS (ns) | TNS (ns) | Status |
|--------|-------------|-----------|----------|----------|--------|
| 6502   | 45%         | 2,450     | 1.2      | 0        | ✓ PASS |
| UART   | 15%         | 850       | 5.8      | 0        | ✓ PASS |
| FPU    | 80%         | 4,200     | -0.5     | -12.3    | ✗ FAIL |

*WNS = Worst Negative Slack, TNS = Total Negative Slack*

## Visualizations

### Placement Density
![Placement Density](build/6502/visualizations/placement_density.png)

### Clock Tree
![CTS Tree](build/6502/visualizations/cts_tree.png)

### Critical Path
![Critical Path](build/6502/visualizations/critical_path.png)

## Requirements

- Python 3.8+
- OpenROAD
- Magic VLSI (optional, for layout viewing)
- KLayout (optional, for layout viewing)


## Repository Structure

```
.
├── src/
│   ├── parsers/           # Platform and netlist parsers
│   ├── placer.py          # Placement algorithms
│   ├── cts.py             # Clock tree synthesis
│   ├── eco_generator.py   # ECO netlist generation
│   ├── visualize.py       # All visualization utilities
│   └── utils.py           # Helper functions
├── scripts/
│   ├── route.tcl          # OpenROAD routing script
│   ├── sta.tcl            # Timing analysis script
│   └── make_def.py        # DEF file generator
├── inputs/
│   ├── platform/          # Platform files
│   └── designs/           # Design files
├── build/                 # Generated files (gitignored)
├── Makefile              # Build automation
├── requirements.txt
└── README.md
```

## Development

We use a standard GitHub workflow:
- Issues for task tracking
- Feature branches for development
- Pull requests with code review
- Protected `main` branch

## License

## Contributors

- Ramy Shehata
- Seif Elansary
- Mohamed Mansour