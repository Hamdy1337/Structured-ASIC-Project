# route.tcl - Generic OpenROAD routing script
# Usage: openroad -exit route.tcl

# Check for required environment variables
set required_vars {DESIGN_NAME LEF_FILES LIB_FILES DEF_FILE OUTPUT_DIR}
foreach var $required_vars {
    if {![info exists ::env($var)]} {
        puts "Error: Environment variable $var is not set."
        exit 1
    }
}

# 1. Read Inputs
puts "\[Generic-Route\] Reading configuration..."

# Read LEF files (can be a list/string separated by spaces)
foreach lef $::env(LEF_FILES) {
    if {[file exists $lef]} {
        read_lef $lef
    } else {
        puts "Warning: LEF file $lef not found"
    }
}

# Read Liberty files
foreach lib $::env(LIB_FILES) {
    if {[file exists $lib]} {
        read_liberty $lib
    } else {
        puts "Warning: LIB file $lib not found"
    }
}

# Read Def
read_def $::env(DEF_FILE)

# Read Verilog (Optional but recommended for full connectivity check)
if {[info exists ::env(VERILOG_FILE)]} {
    read_verilog $::env(VERILOG_FILE)
}

# Link Design
set design_name $::env(DESIGN_NAME)
link_design $design_name

# 2. Global Routing
puts "\[Generic-Route\] Starting Global Route..."
# Set global routing adjustment if env var exists, else default
if {[info exists ::env(GR_ADJUST)]} {
    set_global_routing_layer_adjustment * $::env(GR_ADJUST)
}
global_route -congestion_iterations 50 -verbose

# 3. Detailed Routing
puts "\[Generic-Route\] Starting Detailed Route..."
detailed_route -output_drc $::env(OUTPUT_DIR)/${design_name}_drc.rpt \
               -output_maze $::env(OUTPUT_DIR)/${design_name}_maze.log \
               -output_guide $::env(OUTPUT_DIR)/${design_name}.guide

# 4. Extract Parasitics
puts "\[Generic-Route\] Extracting Parasitics..."
# Check for RCX rules env var
if {[info exists ::env(RCX_RULES_FILE)]} {
    extract_parasitics -ext_model_file $::env(RCX_RULES_FILE)
} else {
    puts "Warning: RCX_RULES_FILE not set. Running extract_parasitics with default/loaded tech info."
    # Fallback or just run it if tech matched
    extract_parasitics
}

# 5. Report Congestion
puts "\[Generic-Route\] Reporting Congestion..."
report_congestion -histogram > $::env(OUTPUT_DIR)/${design_name}_congestion.rpt

# Save Outputs
puts "\[Generic-Route\] Saving outputs..."
write_def $::env(OUTPUT_DIR)/${design_name}_routed.def
write_db $::env(OUTPUT_DIR)/${design_name}_routed.odb

puts "\[Generic-Route\] Completed."
