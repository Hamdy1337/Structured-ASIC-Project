# Structured ASIC Physical Design Flow

A complete automated physical design toolchain for Structured ASIC platforms, from netlist to placement optimization.

## Overview

This repository contains a full Place & Route (PnR) flow for Structured ASICs. Unlike traditional ASICs where cells can be placed anywhere, Structured ASICs use pre-fabricated wafers with fixed logic cell locations. Our flow solves the complex assignment problem of mapping logical gates to physical fabric slots, optimizing for minimal wirelength.

**Key Features:**
- **Multi-Stage Placement**: Greedy initial placement + Simulated Annealing optimization
- **Reinforcement Learning Enhancement**: Optional PPO-based placement refinement for further optimization
- **Design Validation**: Automated validation to ensure designs fit on available fabric
- **Rich Visualizations**: Interactive HTML layouts, placement heatmaps, and training plots
- **Multiple Design Support**: Tested on 6502 CPU, arithmetic units, AES-128, and Z80 designs

## Quick Start

```bash
# Clone the repository
git clone <repo-url>
cd Structred-ASIC-Project

# Create virtual environment and install dependencies
make venv
make install

# Validate a design
make validate

# Run placement for a design (defaults to 6502)
make placer

# Generate visualizations
make visualize DESIGN=6502

# Or run Phase 1 (validation + visualization)
make phase1
```

## Architecture

The flow consists of several major stages, with Phase 1 and Phase 2 fully implemented:

### Phase 1: Database & Validation ✅
Parses platform files (fabric cells, pins, YAML configurations) and design netlists. Validates that the design can fit on the available fabric by checking cell availability.

**Implementation:**
- `src/parsers/` - Complete parser suite for fabric, pins, and netlist files
- `src/validation/validator.py` - Design validation with detailed utilization reports

**Outputs:**
- Fabric utilization report (console output)
- Interactive HTML layout visualization (`build/structured_asic_layout.html`)
- CSV files with parsed data

### Phase 2: Placement ✅
Maps logical cells to physical fabric slots to minimize wirelength (HPWL - Half-Perimeter Wirelength).

**Algorithms Implemented:**

1. **Greedy Initial Placement** (`src/placement/placer.py`):
   - I/O-driven seed & grow algorithm
   - Port-to-pin assignment for fixed I/O cells
   - Dependency-level-based placement ordering
   - Median-of-drivers target location calculation
   - Manhattan distance-based site selection

2. **Simulated Annealing Optimization** (`src/placement/simulated_annealing.py`):
   - Hybrid move set: local refinement (70%) + global exploration (30%)
   - Configurable annealing schedule (temperature, cooling rate)
   - Level-by-level batch annealing for efficiency
   - Tunable parameters for quality vs. runtime trade-offs

**Outputs:**
- Placement CSV file (`build/<design>/<design>_placement.csv`)
- Placement density heatmap (`build/<design>/<design>_placement_heatmap.png`)
- HPWL metrics and validation reports

### Phase 3: Clock Tree Synthesis (CTS) & ECO 🚧
*In development* - Will build balanced clock trees using available buffers and generate ECO netlists.

### Phase 4-5: Routing & STA 🚧
*In development* - Will integrate with OpenROAD for routing and perform static timing analysis.

## Usage

### Available Makefile Targets

```bash
# Setup
make venv          # Create Python virtual environment (.venv)
make install       # Install dependencies from requirements.txt

# Phase 1: Validation & Visualization
make validate      # Validate design fits on fabric
make visualize     # Generate interactive HTML layout (set DESIGN=name for specific design)
make phase1        # Run both validate and visualize

# Phase 2: Placement
make placer        # Run Greedy + Simulated Annealing placement

# Parsers (run individually if needed)
make parsers       # Run all parser scripts

# Cleanup
make clean         # Remove __pycache__ and virtual environment
```

### Running Placement Directly

You can also run the placement algorithm directly:

```bash
# Using Python module
python -m src.placement.placer

# Or modify the design in src/placement/placer.py (line 379)
# Default design: 6502_mapped.json
```


## Input Files

### Platform Files (Static)
Located in `inputs/Platform/`:
- `fabric.yaml` - Fabric configuration and dimensions
- `fabric_cells.yaml` - Complete fabric database with all cell slots and types
- `pins.yaml` - I/O pin locations and metal layer information
- `fabric.lib` - Cell library definitions
- `sky130_fd_sc_hd.lef` - Physical abstracts for all cells (LEF format)
- `sky130_fd_sc_hd.tlef` - Technology LEF file

### Design Files (Per Design)
Located in `inputs/designs/`:
- `<design>_mapped.json` - Logical netlist from Yosys (JSON format)
  - Available designs: `6502_mapped.json`, `arith_mapped.json`, `aes_128_mapped.json`, `z80_mapped.json`

## Output Files

All generated files are organized in `build/`:

### Current Outputs (Phase 1 & 2)

```
build/
├── structured_asic_layout.html          # Interactive Plotly visualization of fabric
├── pins_output.csv                      # Parsed pin information
├── <design>/
│   ├── <design>_placement.csv          # Final placement mapping (cell_name, x_um, y_um, site_id, etc.)
│   └── <design>_placement_heatmap.png  # 2D density heatmap of placed cells
│
```

### Placement CSV Format

The placement CSV (`<design>_placement.csv`) contains:
- `cell_name`: Logical cell name from netlist
- `x_um`, `y_um`: Physical coordinates in micrometers
- `site_id`: Assigned fabric site ID
- `cell_type`: Type of cell (NAND2, OR2, DFF, etc.)
- Additional metadata columns

## Placement Algorithm Configuration

### Simulated Annealing Parameters

The placer uses Simulated Annealing with several tunable parameters (configurable in `src/placement/placer.py`):

| Parameter | Description | Default | Notes |
|-----------|-------------|---------|-------|
| `sa_moves_per_temp` | Moves attempted per temperature step | 200 | Higher = better quality, slower |
| `sa_cooling_rate` | Cooling rate (alpha) | 0.90 | Higher = slower cooling, better quality |
| `sa_T_initial` | Initial temperature | Auto-calculated | Based on initial HPWL if None |
| `sa_p_refine` | Probability of local refinement move | 0.7 | Should sum to 1.0 with p_explore |
| `sa_p_explore` | Probability of global exploration move | 0.3 | Should sum to 1.0 with p_refine |
| `sa_refine_max_distance` | Max Manhattan distance for refine moves (μm) | 100.0 | Limits local search radius |
| `sa_W_initial` | Initial exploration window (fraction of die) | 0.5 | 50% of die width/height |
| `sa_seed` | Random seed for reproducibility | 42 | Set for deterministic results |



## Supported Designs

The flow has been tested on the following designs:

| Design | Description | Status |
|--------|-------------|--------|
| **6502** | 8-bit microprocessor | ✅ Tested |
| **arith** | Arithmetic unit | ✅ Tested |
| **aes_128** | AES-128 encryption core | ✅ Tested |
| **z80** | Z80 microprocessor | ✅ Tested |

### Example: 6502 Placement Results

The 6502 design has been successfully placed with:
- Placement heatmap visualization available at `build/6502/6502_placement_heatmap.png`
- Placement CSV with all cell assignments at `build/6502/6502_placement.csv`
- Interactive fabric layout at `build/structured_asic_layout.html`

*Note: Timing analysis (WNS/TNS) will be available once Phase 4-5 (Routing & STA) are implemented.*

## Visualizations

### Interactive Fabric Layout
The main visualization is an interactive HTML file generated using Plotly:
- **File**: `build/structured_asic_layout.html`
- **Features**:
  - Die and core outlines
  - All fabric cells color-coded by type (NAND, OR, DFF, buffers, etc.)
  - I/O pins with metal layer information
  - Zoom, pan, and hover interactions
  - Legend for cell type identification

**To view**: Open `build/structured_asic_layout.html` in a web browser for full interactivity.

*Note: This is an interactive HTML file, not a static image. It provides zoom, pan, and hover capabilities to explore the fabric layout.*

### Placement Density Heatmap
2D histogram showing cell placement density across the chip:
- **File**: `build/<design>/<design>_placement_heatmap.png`
- Generated automatically after placement
- Example: `build/6502/6502_placement_heatmap.png`

![Placement Heatmap](build/6502/6502_placement_heatmap.png)


## Requirements

### Core Dependencies
- **Python 3.8+** (tested with Python 3.13)
- **Virtual Environment**: The Makefile automatically creates and manages a `.venv`

### Python Packages (see `requirements.txt`)
- `pandas` - Data manipulation
- `numpy` - Numerical computations
- `matplotlib` - Plotting and heatmaps
- `plotly` - Interactive visualizations
- `pyyaml` - YAML file parsing


### External Tools (Future Phases)
- **OpenROAD** - For routing (Phase 4)
- **Magic VLSI / KLayout** - For layout viewing (optional)


## Repository Structure

```
.
├── src/
│   ├── parsers/                    # Platform and netlist parsers
│   │   ├── fabric_db.py           # Main fabric database loader
│   │   ├── fabric_parser.py       # Fabric YAML parser
│   │   ├── fabric_cells_parser.py # Fabric cells parser
│   │   ├── pins_parser.py         # Pin locations parser
│   │   └── netlist_parser.py      # Netlist JSON parser
│   │
│   ├── placement/                  # Placement algorithms
│   │   ├── placer.py              # Main Greedy+SA placer
│   │   ├── placer_rl.py           # PPO-based RL placement
│   │   ├── ppo_driver.py          # PPO training and application driver
│   │   ├── simulated_annealing.py # SA optimization engine
│   │   ├── placement_utils.py     # HPWL, site building utilities
│   │   ├── port_assigner.py       # I/O port-to-pin assignment
│   │   └── dependency_levels.py   # Dependency levelization
│   │
│   ├── validation/                 # Design validation
│   │   ├── validator.py           # Main design validator
│   │   └── placement_validator.py # Placement validation
│   │
│   ├── Visualization/              # Visualization tools
│   │   ├── sasics_visualisation.py # Interactive Plotly layout
│   │   ├── heatmap.py             # Placement density heatmaps
│   │   └── rl_training_plot.py    # PPO training curve plots
│   │
│   ├── cts.py                      # Clock tree synthesis (in development)
│   ├── eco_generator.py            # ECO netlist generation (in development)
│   └── utils.py                    # General utilities
│
├── scripts/
│   ├── route.tcl                   # OpenROAD routing script (future)
│   ├── sta.tcl                     # Timing analysis script (future)
│   └── make_def.py                 # DEF file generator (future)
│
├── inputs/
│   ├── Platform/                   # Platform files (static)
│   │   ├── fabric.yaml
│   │   ├── fabric_cells.yaml
│   │   ├── pins.yaml
│   │   └── sky130_fd_sc_hd.*       # LEF/TLEF files
│   └── designs/                     # Design netlists
│       ├── 6502_mapped.json
│       ├── arith_mapped.json
│       ├── aes_128_mapped.json
│       └── z80_mapped.json
│
├── build/                           # Generated files (gitignored)
│   ├── <design>/                    # Per-design outputs
│   └── structured_asic_layout.html  # Interactive visualization
│
├── Makefile                         # Build automation
├── requirements.txt                 # Python dependencies
├── Project_Description.md           # Original project specification
└── README.md                        # This file
```

## Development Workflow

This project follows a professional GitHub-based workflow:
- **Issues**: All tasks tracked via GitHub Issues
- **Feature Branches**: Development on feature branches (e.g., `feature/cts-htree`)
- **Pull Requests**: Code review required before merging
- **Protected Branches**: `main` branch protected with PR requirements


## Implementation Status

| Phase | Component | Status | Notes |
|-------|-----------|--------|-------|
| **Phase 1** | Database & Validation | ✅ Complete | Full parser suite, validation, interactive visualization |
| **Phase 2** | Placement (Greedy+SA) | ✅ Complete | Production-ready with tunable parameters |
| **Phase 2** | Placement (PPO Refinement) | ✅ Complete | Optional RL-based improvement |
| **Phase 3** | CTS & ECO | 🚧 In Development | Framework in place |
| **Phase 4-5** | Routing & STA | 🚧 Planned | OpenROAD integration planned |

## Contributors

- **Ramy Shehata**
- **Seif Elansary**
- **Mohamed Mansour**

## License

See `LICENSE` file for details.