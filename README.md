# Structured ASIC Placement & Routing Project

This project implements a custom placement and routing flow for a structured ASIC fabric. It features a hybrid placement engine combining **Simulated Annealing (SA)** and **Reinforcement Learning (RL)** to optimize cell placement, followed by handling Clock Tree Synthesis (CTS) and routing interfaces.

## 🚀 Key Features
- **Hybrid Placement Engine**:
  - **Greedy Initialization**: Fast initial placement based on connectivity.
  - **Simulated Annealing (SA)**: Global optimization to escape local minima.
  - **RL Refinement (PPO)**: Deep Reinforcement Learning (PPO) agent for final fine-tuning and congestion management.
- **Custom Fabric Support**: Parsers for custom fabric definitions (`fabric_cells.yaml`).
- **Visualizations**: Automatic generation of placement heatmaps and animations (`.mp4`, `.gif`).
- **ECO Flow**: Integration for post-placement changes and buffer insertion.

## 📂 Project Structure

### `src/` - Core Source Code
The heart of the project, organized by functionality:

- **`src/placement/`**: The heavy lifters for placement.
  - `placer.py`: Main entry point for Standard/SA placement.
  - `placer_rl.py`: RL-based placement logic using PPO.
  - `simulated_annealing.py`: Core SA algorithm implementation.
  - `ppo_driver.py`: Training loop for the PPO agent.
  - `port_assigner.py`: Smart I/O port mapping to fabric pins.

- **`src/parsers/`**: Input handling.
  - `fabric_parser.py` / `fabric_cells_parser.py`: Reads the ASIC fabric architecture.
  - `netlist_parser.py`: Parses synthesized Verilog netlists.
  - `pins_parser.py` / `lef_parser.py`: Physical constraint parsing.

- **`src/routing/`**: Routing interfaces.
  - `route.tcl`: Tcl scripts for driving external routers (e.g., OpenROAD/TritonRoute).

- **`src/cts/`**: Clock Tree Synthesis.
  - `htree_builder.py`: H-Tree generation for balanced clock distribution.

- **`src/validation/`**: Quality assurance.
  - Checks for overlaps, valid sites, and connectivity integrity.

### `scripts/` - Automation & Utilities
Helper scripts for the physical design flow:
- `generate_def.py`: Converts placed results into industry-standard DEF format.
- `debug_sta.ps1` / `sta.tcl`: Static Timing Analysis scripts.
- `debug_view.ps1`: Launchers for viewing designs in KLayout/OpenROAD.

### `inputs/` - Design Data
- `designs/`: Netlists for benchmark designs (6502, Z80, etc.).
- `Platform/`: Fabric definitions (`fabric.yaml`, `fabric_cells.yaml`).

### `build/` - Outputs
All run artifacts are generated here, organized by design name (e.g., `build/6502/`).
- **`*_final.v`**: The final placed netlist.
- **`*_placement.csv`**: Raw placement data.
- **`*_placement_animation.mp4`**: Visualization of the placement process.
- **`*.def`**: DEF file for routing tools.

---

## 🏃‍♂️ How to Run

### 1. Standard Flow (Greedy + SA)
Runs constructive placement followed by Simulated Annealing. Best for general use.
```powershell
# Run the 6502 processor flow
python -m run_6502_flow

# Run the Z80 processor flow
python -m run_z80_flow
```

### 2. RL-Based Flow (Greedy + SA + PPO)
Runs the full hybrid stack: Greedy -> SA -> PPO Refinement. Best for maximum optimization of difficult designs.
```powershell
# Run the 6502 flow with RL refinement
python -m run_6502_rl_flow
```

### 3. Debugging & Animation
Animations are generated automatically if enabled.
- Check `build/<design>/sa_animation_frames/` for individual frames.
- Check `build/<design>/<design>_placement_animation.mp4` for the final video.

## 🛠 Prerequisites
- Python 3.8+
- Required packages (install via `pip install -r requirements.txt`):
  - `numpy`, `pandas`, `pyyaml`
  - `torch` (for RL flow)
  - `imageio`, `imageio-ffmpeg` (for animations)
  - `tqdm` (for progress bars)
