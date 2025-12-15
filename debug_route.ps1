$env:DESIGN_NAME = "arith"
$env:MERGED_LEF = "inputs/Platform/sky130_fd_sc_hd.merged.lef"
$env:LIB_FILES = "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
$env:VERILOG_FILE = "build/arith/arith_renamed.v"
$env:DEF_FILE = "build/arith/arith_fixed.def"
$env:OUTPUT_DIR = "build/arith"

Write-Output "Preparing files for routing..."

# 1. Ensure Map File is accessible as arith.map for rename.py
if (Test-Path "build/arith/arith_eco.map") {
    Copy-Item "build/arith/arith_eco.map" "build/arith/arith.map" -Force
}
else {
    Write-Error "build/arith/arith_eco.map not found. Run run_arith_flow.py first."
    exit 1
}

# 2. Generate DEF (Physical Placement)
Write-Output "Generating DEF..."
python scripts/generate_def.py `
    --design_name arith `
    --fabric_cells inputs/Platform/fabric_cells.yaml `
    --pins inputs/Platform/pins.yaml `
    --map build/arith/arith.map `
    --fabric_def inputs/Platform/fabric.yaml `
    --output build/arith/arith_fixed.def

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 3. Rename Verilog Instances AND Modules to match Physical LEF
Write-Output "Renaming Verilog instances and modules..."
python src/routing/rename.py arith --fabric inputs/Platform/fabric.yaml

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 4. Run OpenROAD Routing (via Docker)
Write-Output "Running OpenROAD Routing (via Docker)..."

# 4a. Get WSL IP for X11 Display
try {
    $IP = Get-NetIPAddress -InterfaceAlias 'vEthernet (WSL)' -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress
    Write-Output "Detected WSL IP for Display: $IP"
}
catch {
    Write-Warning "Could not detect 'vEthernet (WSL)' interface. GUI might not work. Defaulting DISPLAY to host.docker.internal:0.0"
    $IP = "host.docker.internal"
}

# 4b. Run Docker
# Use the full path to openroad binary as specified by user
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
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -gui -exit src/routing/route.tcl
