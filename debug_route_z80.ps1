# debug_route_z80.ps1 - Route the z80 design

$env:DESIGN_NAME = "z80"
$env:MERGED_LEF = "inputs/Platform/sky130_fd_sc_hd.merged.lef"
$env:LIB_FILES = "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
$env:VERILOG_FILE = "build/z80/z80_renamed.v"
$env:DEF_FILE = "build/z80/z80_fixed.def"
$env:OUTPUT_DIR = "build/z80"

Write-Output "=============================================="
Write-Output "Routing z80 Design"
Write-Output "=============================================="

# 1. Check that flow was run first
if (-not (Test-Path "build/z80/z80_eco.map")) {
    Write-Error "build/z80/z80_eco.map not found."
    Write-Error "Run 'python -m run_z80_flow' first."
    exit 1
}

Write-Output "Preparing files for routing..."

# 2. Ensure Map File is accessible for rename.py
Copy-Item "build/z80/z80_eco.map" "build/z80/z80.map" -Force

# 3. Generate DEF (Physical Placement)
Write-Output "Generating DEF..."
python scripts/generate_def.py `
    --design_name z80 `
    --fabric_cells inputs/Platform/fabric_cells.yaml `
    --pins inputs/Platform/pins.yaml `
    --map build/z80/z80.map `
    --fabric_def inputs/Platform/fabric.yaml `
    --output build/z80/z80_fixed.def

if ($LASTEXITCODE -ne 0) { 
    Write-Error "DEF generation failed!"
    exit $LASTEXITCODE 
}

# 4. Rename Verilog Instances AND Modules to match Physical LEF
Write-Output "Renaming Verilog instances and modules..."
python src/routing/rename.py z80 --fabric inputs/Platform/fabric.yaml

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
    openroad/orfs:latest `
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -no_init -exit src/routing/route.tcl

Write-Output ""
Write-Output "=============================================="
Write-Output "Routing Complete for z80"
Write-Output "=============================================="
Write-Output "Output files:"
Write-Output "  - build/z80/z80_routed.def"
Write-Output "  - build/z80/z80_routed.odb"
Write-Output "  - build/z80/z80_drc.rpt"
