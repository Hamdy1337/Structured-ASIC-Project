# debug_view.ps1 - Open OpenROAD GUI to view an existing routed design
# This script opens the GUI without running any flow - just for viewing results

$env:DESIGN_NAME = "arith"

# Optional: Set these if you want to specify exact files
# $env:ODB_FILE = "build/arith/arith_routed.odb"
# $env:DEF_FILE = "build/arith/arith_routed.def"
# $env:VERILOG_FILE = "build/arith/arith_renamed.v"
# $env:MERGED_LEF = "inputs/Platform/sky130_fd_sc_hd.merged.lef"
# $env:LIB_FILES = "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
# $env:EXTRACT_SPEF = "1"  # Set to "1" to force SPEF extraction (even if file exists)
$env:OUTPUT_DIR = "build/$env:DESIGN_NAME"

# Set EXTRACT_SPEF to empty if not defined (script will auto-detect)
if (-not $env:EXTRACT_SPEF) {
    $env:EXTRACT_SPEF = ""
}

# Auto-detect RCX rules file if not set
if (-not $env:RCX_RULES_FILE) {
    $possibleRcxFiles = @(
        "inputs/Platform/rcx_patterns.rules",
        "inputs/Platform/sky130_rcx_rules",
        "inputs/Platform/sky130hd.rcx",
        "inputs/Platform/rcx_rules"
    )
    
    foreach ($rcxFile in $possibleRcxFiles) {
        if (Test-Path $rcxFile) {
            $env:RCX_RULES_FILE = $rcxFile
            Write-Output "Auto-detected RCX rules file: $rcxFile"
            break
        }
    }
    
    if (-not $env:RCX_RULES_FILE) {
        Write-Output "RCX rules file not found. SPEF extraction will try without RCX file."
        Write-Output "  (To use RCX file, place it at: inputs/Platform/rcx_patterns.rules)"
        Write-Output ""
    }
}

Write-Output "=============================================="
Write-Output "OpenROAD GUI Viewer for $env:DESIGN_NAME"
Write-Output "=============================================="
Write-Output ""
Write-Output "This script opens OpenROAD GUI to view existing design files."
Write-Output "No routing or analysis will be performed - view only."
Write-Output ""

# Detect host IP for X11 Display (XLaunch)
$IP = $null

# Method 1: Try WSL vEthernet interface
try {
    $IP = Get-NetIPAddress -InterfaceAlias 'vEthernet (WSL)' -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty IPAddress
    if ($IP) {
        Write-Output "Detected WSL IP: $IP"
    }
}
catch {
    Write-Output "Method 1 failed: WSL interface not found"
}

# Method 2: Try DockerNAT interface
if (-not $IP) {
    try {
        $IP = Get-NetIPAddress -InterfaceAlias 'vEthernet (DockerNAT)' -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty IPAddress
        if ($IP) {
            Write-Output "Detected DockerNAT IP: $IP"
        }
    }
    catch {
        Write-Output "Method 2 failed: DockerNAT interface not found"
    }
}

# Method 3: Use host.docker.internal (Docker Desktop default)
if (-not $IP) {
    $IP = "host.docker.internal"
    Write-Output "Using fallback: host.docker.internal"
}

# Allow manual override via environment variable
if ($env:X11_DISPLAY_IP) {
    $IP = $env:X11_DISPLAY_IP
    Write-Output "Using manually specified X11 IP from X11_DISPLAY_IP: $IP"
}

$DISPLAY_VAL = "${IP}:0.0"
Write-Output ""
Write-Output "=============================================="
Write-Output "X11 Display Configuration"
Write-Output "=============================================="
Write-Output "DISPLAY=$DISPLAY_VAL"
Write-Output ""
Write-Output "TROUBLESHOOTING: If GUI doesn't work, check:"
Write-Output "  1. XLaunch (or VcXsrv) is running"
Write-Output "  2. XLaunch settings:"
Write-Output "     - Display number: 0"
Write-Output "     - 'Disable access control' CHECKED (important!)"
Write-Output "     - 'Native opengl' UNCHECKED"
Write-Output "  3. Windows Firewall allows XLaunch"
Write-Output "  4. If auto-detection fails, set manually:"
Write-Output '     $env:X11_DISPLAY_IP = "YOUR_WINDOWS_IP"'
Write-Output "     (Find your IP with: ipconfig | findstr IPv4)"
Write-Output ""
Write-Output "=============================================="
Write-Output ""

# Check if design files exist
$outputDir = "build/$env:DESIGN_NAME"
$odbFile = "$outputDir/${env:DESIGN_NAME}_routed.odb"
$defFile = "$outputDir/${env:DESIGN_NAME}_routed.def"
$verilogFile = "$outputDir/${env:DESIGN_NAME}_renamed.v"

Write-Output "Checking for design files..."
$filesFound = @()

if (Test-Path $odbFile) {
    Write-Output "  [OK] Found ODB: $odbFile"
    $filesFound += "ODB"
} else {
    Write-Output "  [X] ODB not found: $odbFile"
}

if (Test-Path $defFile) {
    Write-Output "  [OK] Found DEF: $defFile"
    $filesFound += "DEF"
} else {
    Write-Output "  [X] DEF not found: $defFile"
}

if (Test-Path $verilogFile) {
    Write-Output "  [OK] Found Verilog: $verilogFile"
    $filesFound += "Verilog"
} else {
    Write-Output "  [X] Verilog not found: $verilogFile"
}

if ($filesFound.Count -eq 0) {
    Write-Warning "No design files found. GUI may not be able to load the design."
    Write-Warning "Please ensure you have run routing first (debug_route.ps1)"
    Write-Output ""
    $continue = Read-Host "Continue anyway? (y/n)"
    if ($continue -ne "y" -and $continue -ne "Y") {
        exit 0
    }
}

Write-Output ""
Write-Output "Opening OpenROAD GUI..."
Write-Output ""

# Run Docker with OpenROAD GUI
docker run --rm `
    -v "${PWD}:/project" `
    -w /project `
    -e DISPLAY=$DISPLAY_VAL `
    -e DESIGN_NAME=$env:DESIGN_NAME `
    -e OUTPUT_DIR=$env:OUTPUT_DIR `
    -e MERGED_LEF=$env:MERGED_LEF `
    -e LIB_FILES=$env:LIB_FILES `
    -e VERILOG_FILE=$env:VERILOG_FILE `
    -e DEF_FILE=$env:DEF_FILE `
    -e ODB_FILE=$env:ODB_FILE `
    -e EXTRACT_SPEF=$env:EXTRACT_SPEF `
    -e RCX_RULES_FILE=$env:RCX_RULES_FILE `
    --ipc=host `
    openroad/orfs:latest `
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -gui scripts/view_design.tcl

Write-Output ""
Write-Output "=============================================="
Write-Output "GUI Closed"
Write-Output "=============================================="
