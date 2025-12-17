# debug_sta.ps1 - Run Static Timing Analysis (STA) for a design

$env:DESIGN_NAME = "arith"
$env:MERGED_LEF = "inputs/Platform/sky130_fd_sc_hd.merged.lef"
$env:LIB_FILES = "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
$env:VERILOG_FILE = "build/arith/arith_renamed.v"
$env:OUTPUT_DIR = "build/arith"

# Auto-detect SPEF file (check common naming patterns)
$spefCandidates = @(
    "build/arith/arith.spef",
    "build/arith/arith_rl.spef",
    "build/arith/arith_routed.spef"
)

$env:SPEF_FILE = $null
foreach ($candidate in $spefCandidates) {
    if (Test-Path $candidate) {
        $env:SPEF_FILE = $candidate
        Write-Output "Found SPEF file: $candidate"
        break
    }
}

# If not found, use default
if (-not $env:SPEF_FILE) {
    $env:SPEF_FILE = "build/arith/arith.spef"
    Write-Output "SPEF file not found, will use: $env:SPEF_FILE"
}

# SDC file
$env:SDC_FILE = "build/arith/arith.sdc"

# Optional: Set OUTPUT_SUFFIX if running STA on RL-refined design
# $env:OUTPUT_SUFFIX = "_rl"  # Uncomment if using RL-refined placement
if (-not $env:OUTPUT_SUFFIX) {
    $env:OUTPUT_SUFFIX = ""
}

Write-Output "=============================================="
Write-Output "Static Timing Analysis (STA) for $env:DESIGN_NAME"
Write-Output "=============================================="
Write-Output ""
Write-Output "Prerequisites:"
Write-Output "  1. Design must be routed (run debug_route.ps1 first)"
Write-Output "  2. SPEF file should be generated during routing"
Write-Output "  3. SDC file will be auto-generated if missing"
Write-Output ""

# 1. Check that required files exist
Write-Output "Checking required files..."

$missing_files = @()

if (-not (Test-Path $env:VERILOG_FILE)) {
    $missing_files += $env:VERILOG_FILE
    Write-Error "Verilog file not found: $env:VERILOG_FILE"
    Write-Error "Please run routing step first (debug_route.ps1)"
}

if (-not (Test-Path $env:SPEF_FILE)) {
    $missing_files += $env:SPEF_FILE
    Write-Warning "SPEF file not found: $env:SPEF_FILE"
    Write-Warning "STA will run without parasitic information (less accurate)"
}

if (-not (Test-Path $env:SDC_FILE)) {
    $missing_files += $env:SDC_FILE
    Write-Warning "SDC file not found: $env:SDC_FILE"
    Write-Output "Attempting to generate SDC file (post-route mode)..."
    
    # Try to generate SDC file (post-route since we're running STA after routing)
    python scripts/generate_sdc.py $env:DESIGN_NAME --post-route
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error "SDC generation failed!"
        Write-Error "Please generate SDC file manually or run:"
        Write-Error "  python scripts/generate_sdc.py $env:DESIGN_NAME"
        exit 1
    }
    
    if (Test-Path $env:SDC_FILE) {
        Write-Output "SDC file generated successfully: $env:SDC_FILE"
    } else {
        Write-Error "SDC file still not found after generation"
        exit 1
    }
}

if (-not (Test-Path $env:LIB_FILES)) {
    $missing_files += $env:LIB_FILES
    Write-Error "Liberty file not found: $env:LIB_FILES"
    exit 1
}

if (-not (Test-Path $env:MERGED_LEF)) {
    $missing_files += $env:MERGED_LEF
    Write-Error "Merged LEF file not found: $env:MERGED_LEF"
    Write-Error "LEF file is required for STA (technology information)"
    exit 1
}

if ($missing_files.Count -gt 0 -and $env:SPEF_FILE -in $missing_files) {
    # SPEF is optional (warning only), but others are required
    $required_missing = $missing_files | Where-Object { $_ -ne $env:SPEF_FILE }
    if ($required_missing.Count -gt 0) {
        Write-Error "Required files missing. Please check the errors above."
        exit 1
    }
}

Write-Output "All required files found."
Write-Output ""

# 2. Display configuration
Write-Output "STA Configuration:"
Write-Output "  Design: $env:DESIGN_NAME"
Write-Output "  LEF: $env:MERGED_LEF"
Write-Output "  Liberty: $env:LIB_FILES"
Write-Output "  Verilog: $env:VERILOG_FILE"
Write-Output "  SPEF: $env:SPEF_FILE"
Write-Output "  SDC: $env:SDC_FILE"
Write-Output "  Output: $env:OUTPUT_DIR"
Write-Output ""

# 3. Run OpenROAD STA (via Docker)
Write-Output "Running OpenROAD STA (via Docker)..."

# Detect host IP for X11 Display (if needed for GUI)
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

# Run Docker with OpenROAD STA
docker run --rm `
    -v "${PWD}:/project" `
    -w /project `
    -e DISPLAY=$DISPLAY_VAL `
    -e DESIGN_NAME=$env:DESIGN_NAME `
    -e MERGED_LEF=$env:MERGED_LEF `
    -e LIB_FILES=$env:LIB_FILES `
    -e VERILOG_FILE=$env:VERILOG_FILE `
    -e SPEF_FILE=$env:SPEF_FILE `
    -e SDC_FILE=$env:SDC_FILE `
    -e OUTPUT_DIR=$env:OUTPUT_DIR `
    -e OUTPUT_SUFFIX=$env:OUTPUT_SUFFIX `
    openroad/orfs:latest `
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -exit scripts/sta.tcl

if ($LASTEXITCODE -ne 0) {
    Write-Error "STA failed with exit code: $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Output ""
Write-Output "=============================================="
Write-Output "STA Complete for $env:DESIGN_NAME"
Write-Output "=============================================="
Write-Output "Output files:"
Write-Output "  - $env:OUTPUT_DIR/$env:DESIGN_NAME`_setup.rpt"
Write-Output "  - $env:OUTPUT_DIR/$env:DESIGN_NAME`_hold.rpt"
Write-Output "  - $env:OUTPUT_DIR/$env:DESIGN_NAME`_clock_skew.rpt"
Write-Output "  - $env:OUTPUT_DIR/$env:DESIGN_NAME`_timing_summary.rpt"
Write-Output ""

