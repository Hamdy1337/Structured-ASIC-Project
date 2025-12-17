# debug_route_rl.ps1 - Route the RL-refined placement for arith
# This script is for routing designs placed with the RL placer (run_arith_rl_flow.py)

param(
    [string]$DesignName = "arith"
)

$env:DESIGN_NAME = $DesignName
$env:MERGED_LEF = "inputs/Platform/sky130_fd_sc_hd.merged.lef"
$env:LIB_FILES = "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
$env:VERILOG_FILE = "build/$DesignName/${DesignName}_renamed.v"
$env:DEF_FILE = "build/$DesignName/${DesignName}_fixed.def"
$env:OUTPUT_DIR = "build/$DesignName"

Write-Output "=============================================="
Write-Output "Routing RL-Refined Placement for: $DesignName"
Write-Output "=============================================="

# 1. Check that RL flow was run first
if (-not (Test-Path "build/$DesignName/${DesignName}_eco.map")) {
    Write-Error "build/$DesignName/${DesignName}_eco.map not found."
    Write-Error "Run 'python run_arith_rl_flow.py' first to generate RL-refined placement."
    exit 1
}

Write-Output "Preparing files for routing..."

# 2. Ensure Map File is accessible for rename.py
Copy-Item "build/$DesignName/${DesignName}_eco.map" "build/$DesignName/${DesignName}.map" -Force

# 3. Generate DEF (Physical Placement) from RL-refined map
Write-Output "Generating DEF from RL-refined placement..."
python scripts/generate_def.py `
    --design_name $DesignName `
    --fabric_cells inputs/Platform/fabric_cells.yaml `
    --pins inputs/Platform/pins.yaml `
    --map "build/$DesignName/${DesignName}.map" `
    --fabric_def inputs/Platform/fabric.yaml `
    --output "build/$DesignName/${DesignName}_fixed.def"

if ($LASTEXITCODE -ne 0) { 
    Write-Error "DEF generation failed!"
    exit $LASTEXITCODE 
}

# 4. Rename Verilog Instances AND Modules to match Physical LEF
Write-Output "Renaming Verilog instances and modules..."
python src/routing/rename.py $DesignName --fabric inputs/Platform/fabric.yaml

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
Write-Output "Routing Complete for RL-Refined: $DesignName"
Write-Output "=============================================="
Write-Output "Output files:"
Write-Output "  - build/$DesignName/${DesignName}_routed.def"
Write-Output "  - build/$DesignName/${DesignName}_routed.odb"
Write-Output "  - build/$DesignName/${DesignName}_drc.rpt"
