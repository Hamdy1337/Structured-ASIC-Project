# debug_route_6502_rl.ps1 - Route the RL-refined placement for 6502
# This script is for routing designs placed with the RL placer (run_6502_rl_flow.py)

$env:DESIGN_NAME = "6502"
$env:MERGED_LEF = "inputs/Platform/sky130_fd_sc_hd.merged.lef"
$env:LIB_FILES = "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
$env:VERILOG_FILE = "build/6502/6502_rl_renamed.v"
$env:DEF_FILE = "build/6502/6502_rl_fixed.def"
$env:OUTPUT_DIR = "build/6502"
$env:OUTPUT_SUFFIX = "_rl"

Write-Output "=============================================="
Write-Output "Routing RL-Refined Placement for 6502"
Write-Output "=============================================="

# 1. Check that RL flow was run first
if (-not (Test-Path "build/6502/6502_rl_eco.map")) {
    Write-Error "build/6502/6502_rl_eco.map not found."
    Write-Error "Run 'python -m run_6502_rl_flow' first to generate RL-refined placement."
    exit 1
}

Write-Output "Preparing files for routing..."

# 2. Ensure Map File is accessible for rename.py
Copy-Item "build/6502/6502_rl_eco.map" "build/6502/6502_rl.map" -Force

# 3. Generate DEF (Physical Placement) from RL-refined map
Write-Output "Generating DEF from RL-refined placement..."
python scripts/generate_def.py `
    --design_name 6502 `
    --fabric_cells inputs/Platform/fabric_cells.yaml `
    --pins inputs/Platform/pins.yaml `
    --map "build/6502/6502_rl.map" `
    --fabric_def inputs/Platform/fabric.yaml `
    --output "build/6502/6502_rl_fixed.def"

if ($LASTEXITCODE -ne 0) { 
    Write-Error "DEF generation failed!"
    exit $LASTEXITCODE 
}

# 4. Rename Verilog Instances AND Modules to match Physical LEF
Write-Output "Renaming Verilog instances and modules..."
python src/routing/rename.py 6502 --fabric inputs/Platform/fabric.yaml --suffix _rl

if ($LASTEXITCODE -ne 0) { 
    Write-Error "Verilog renaming failed!"
    exit $LASTEXITCODE 
}

# 5. Run OpenROAD Routing (via Docker)
Write-Output "Running OpenROAD Routing (via Docker)..."

# Get WSL IP for X11 Display
try {
    $IP = Get-NetIPAddress -InterfaceAlias 'vEthernet (WSL)' -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress
    Write-Output "Detected WSL IP for Display: $IP"
}
catch {
    Write-Warning "Could not detect 'vEthernet (WSL)' interface. GUI might not work. Defaulting DISPLAY to host.docker.internal:0.0"
    $IP = "host.docker.internal"
}

# Run Docker with OpenROAD
docker run --rm `
    -v "${PWD}:/project" `
    -w /project `
    -e DISPLAY=$($IP):0.0 `
    -e DESIGN_NAME=$env:DESIGN_NAME `
    -e MERGED_LEF=$env:MERGED_LEF `
    -e LIB_FILES=$env:LIB_FILES `
    -e VERILOG_FILE=$env:VERILOG_FILE `
    -e DEF_FILE=$env:DEF_FILE `
    -e OUTPUT_DIR=$env:OUTPUT_DIR `
    -e OUTPUT_SUFFIX=$env:OUTPUT_SUFFIX `
    openroad/orfs:latest `
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -gui -exit src/routing/route.tcl

Write-Output ""
Write-Output "=============================================="
Write-Output "Routing Complete for RL-Refined 6502"
Write-Output "=============================================="
Write-Output "Output files:"
Write-Output "  - build/6502/6502_rl_routed.def"
Write-Output "  - build/6502/6502_rl_routed.odb"
Write-Output "  - build/6502/6502_rl_drc.rpt"
