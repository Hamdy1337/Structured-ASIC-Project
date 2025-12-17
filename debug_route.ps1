$env:DESIGN_NAME = "arith"
$env:MERGED_LEF = "inputs/Platform/sky130_fd_sc_hd.merged.lef"
$env:LIB_FILES = "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
$env:VERILOG_FILE = "build/arith/arith_renamed.v"
$env:DEF_FILE = "build/arith/arith_fixed.def"
$env:OUTPUT_DIR = "build/arith"

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
}

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

# 4a. Detect host IP for X11 Display (XLaunch)
# Try multiple methods to find the Windows host IP
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

# Method 3: Get default gateway (usually the host)
if (-not $IP) {
    try {
        $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($route) {
            $IP = $route.NextHop
            Write-Output "Detected default gateway IP: $IP"
        }
    }
    catch {
        Write-Output "Method 3 failed: Could not get default gateway"
    }
}

# Method 4: Use host.docker.internal (Docker Desktop default)
if (-not $IP) {
    $IP = "host.docker.internal"
    Write-Output "Using fallback: host.docker.internal"
}

# Method 5: Get Windows host IP from Docker network gateway
if (-not $IP -or $IP -eq "host.docker.internal") {
    try {
        # Get Docker network info to find gateway
        $dockerNetwork = docker network inspect bridge 2>$null | ConvertFrom-Json
        if ($dockerNetwork -and $dockerNetwork[0].IPAM.Config -and $dockerNetwork[0].IPAM.Config[0].Gateway) {
            $gatewayIP = $dockerNetwork[0].IPAM.Config[0].Gateway
            # The gateway is usually the host, but we need the actual Windows IP
            # Try to get the IP of the interface that Docker uses
            $adapter = Get-NetAdapter | Where-Object { $_.Name -like "*Docker*" -or $_.Name -like "*Hyper-V*" } | Select-Object -First 1
            if ($adapter) {
                $dockerIP = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty IPAddress
                if ($dockerIP) {
                    Write-Output "Detected Docker adapter IP: $dockerIP"
                    # Use the IP of the adapter that's on the same network as Docker gateway
                    $IP = $dockerIP
                }
            }
        }
    }
    catch {
        Write-Output "Method 5 failed: Could not get Docker network info"
    }
}

# Method 6: Try to resolve host.docker.internal to actual IP
if ($IP -eq "host.docker.internal" -or -not $IP) {
    try {
        $resolved = [System.Net.Dns]::GetHostAddresses("host.docker.internal") | Select-Object -First 1
        if ($resolved) {
            $IP = $resolved.IPAddressToString
            Write-Output "Resolved host.docker.internal to: $IP"
        }
        else {
            $IP = "host.docker.internal"
        }
    }
    catch {
        Write-Output "Could not resolve host.docker.internal, using as-is"
        $IP = "host.docker.internal"
    }
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
Write-Output "     `$env:X11_DISPLAY_IP = 'YOUR_WINDOWS_IP'"
Write-Output "     (Find your IP with: ipconfig | findstr IPv4)"
Write-Output ""
Write-Output "=============================================="
Write-Output ""

# 4b. Run Docker with X11 forwarding
# For XLaunch on Windows, we need to:
# 1. Set DISPLAY to Windows host IP
# 2. Use --network host OR mount X11 socket (Docker Desktop handles this automatically)
docker run --rm `
    -v "${PWD}:/project" `
    -w /project `
    -e DISPLAY=$DISPLAY_VAL `
    -e DESIGN_NAME=$env:DESIGN_NAME `
    -e MERGED_LEF=$env:MERGED_LEF `
    -e LIB_FILES=$env:LIB_FILES `
    -e VERILOG_FILE=$env:VERILOG_FILE `
    -e DEF_FILE=$env:DEF_FILE `
    -e OUTPUT_DIR=$env:OUTPUT_DIR `
    -e RCX_RULES_FILE=$env:RCX_RULES_FILE `
    --ipc=host `
    openroad/orfs:latest `
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -gui src/routing/route.tcl
