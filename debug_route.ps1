$env:DESIGN_NAME="z80"
$env:TECH_LEF="inputs/Platform/sky130_fd_sc_hd.tlef"
$env:LEF_FILES="inputs/Platform/sky130_fd_sc_hd.lef"
$env:LIB_FILES="inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
$env:VERILOG_FILE="build/z80/z80_final_fixed.v"
$env:DEF_FILE="build/z80/z80_fixed.def"
$env:OUTPUT_DIR="build/z80"

echo "Running OpenROAD Routing..."
openroad -exit src/routing/route.tcl
