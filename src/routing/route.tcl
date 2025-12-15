# route.tcl - Generic OpenROAD routing script
# Usage: openroad -exit route.tcl

# Check for required environment variables
# Notes:
# - DEF has no NETS, so VERILOG_FILE is required for connectivity.
# - Provide either MERGED_LEF (single file) or LEF_FILES (space-separated list).
# - TECH_LEF is optional; when provided it is loaded first (under a temp name).
set required_vars {DESIGN_NAME LIB_FILES DEF_FILE VERILOG_FILE OUTPUT_DIR}
foreach var $required_vars {
    if {![info exists ::env($var)]} {
        puts "Error: Environment variable $var is not set."
        exit 1
    }
    }

set has_merged_lef 0
if {[info exists ::env(MERGED_LEF)] && [file exists $::env(MERGED_LEF)]} {
    set has_merged_lef 1
}
set has_lef_files 0
if {[info exists ::env(LEF_FILES)]} {
    set lef_list [split $::env(LEF_FILES) " "]
    if {[llength $lef_list] > 0} {
        set has_lef_files 1
    }
}
if {!$has_merged_lef && !$has_lef_files} {
    puts "Error: Provide MERGED_LEF (existing file) or LEF_FILES (one or more files)."
    exit 1
}


# 1. Read Inputs
puts "\[Generic-Route\] Reading configuration..."

# Read LEF files
if {[info exists ::env(TECH_LEF)]} {
    if {[file exists $::env(TECH_LEF)]} {
        # OpenROAD infers library name from filename. TLEF and LEF share "sky130_fd_sc_hd" basename.
        # Copy TLEF to a temp name to register it as a different library key in ODB.
        set temp_tech_lef "build/tech_renamed.lef"
        
        # Ensure build directory exists (though it should)
        file mkdir "build"
        
        file copy -force $::env(TECH_LEF) $temp_tech_lef
        puts "Reading Tech LEF: $temp_tech_lef (renamed from $::env(TECH_LEF))"
        read_lef $temp_tech_lef
    } else {
        puts "Warning: Tech LEF file $::env(TECH_LEF) not found"
    }
}

if {$has_merged_lef} {
    puts "Reading Merged LEF: $::env(MERGED_LEF)"
    read_lef $::env(MERGED_LEF)
} else {
    foreach lef [split $::env(LEF_FILES) " "] {
        if {[file exists $lef]} {
            puts "Reading LEF: $lef"
            read_lef $lef
        } else {
            puts "Error: LEF file $lef not found"
            exit 1
        }
    }
}

# Read Liberty files
foreach lib [split $::env(LIB_FILES) " "] {
    if {[file exists $lib]} {
        read_liberty $lib
    } else {
        puts "Warning: LIB file $lib not found"
    }
}

# Read Verilog - REQUIRED for connectivity since DEF lacks NETS
read_verilog $::env(VERILOG_FILE)

# Link Design
set design_name $::env(DESIGN_NAME)
link_design $design_name

# Read Def (Apply placement to the linked design)
# Use -floorplan_initialize to apply to existing block from link_design
read_def -floorplan_initialize $::env(DEF_FILE)

# Explicitly generate tracks since they are missing from TLEF/DEF
make_tracks li1 -x_offset 0.23 -x_pitch 0.46 -y_offset 0.17 -y_pitch 0.34
make_tracks met1 -x_offset 0.17 -x_pitch 0.34 -y_offset 0.17 -y_pitch 0.34
make_tracks met2 -x_offset 0.23 -x_pitch 0.46 -y_offset 0.23 -y_pitch 0.46
make_tracks met3 -x_offset 0.34 -x_pitch 0.68 -y_offset 0.34 -y_pitch 0.68
make_tracks met4 -x_offset 0.46 -x_pitch 0.92 -y_offset 0.46 -y_pitch 0.92
make_tracks met5 -x_offset 0.80 -x_pitch 1.60 -y_offset 0.80 -y_pitch 1.60

# 2. Global Routing
puts "\[Generic-Route\] Starting Global Route..."
# Set global routing adjustment if env var exists, else default
if {[info exists ::env(GR_ADJUST)]} {
    set_global_routing_layer_adjustment * $::env(GR_ADJUST)
}

if {[catch {global_route -congestion_iterations 100 -verbose} error_msg]} {
    puts "\[Generic-Route\] Global Routing failed with error: $error_msg"
    puts "\[Generic-Route\] Saving partial database for debugging..."
    write_db $::env(OUTPUT_DIR)/${design_name}_failed_route.odb
    exit 1
}

# 3. Detailed Routing
puts "\[Generic-Route\] Starting Detailed Route..."
set drc_rpt $::env(OUTPUT_DIR)/${design_name}_drc.rpt
detailed_route \
               -bottom_routing_layer met1 \
               -top_routing_layer met5 \
               -output_drc $drc_rpt \
               -output_maze $::env(OUTPUT_DIR)/${design_name}_maze.log \
               -output_guide $::env(OUTPUT_DIR)/${design_name}.guide

# 4. Extract Parasitics
puts "\[Generic-Route\] Skipping parasitic extraction due to missing RCX rules."
# if {[info exists ::env(RCX_RULES_FILE)]} {
#     extract_parasitics -ext_model_file $::env(RCX_RULES_FILE)
# } else {
#     puts "Warning: RCX_RULES_FILE not set. Running extract_parasitics with default/loaded tech info."
#     # Fallback or just run it if tech matched
#     # extract_parasitics
# }

# 5. Report Congestion
puts "\[Generic-Route\] Reporting Congestion..."
report_congestion -histogram > $::env(OUTPUT_DIR)/${design_name}_congestion.rpt

# Save Outputs
puts "\[Generic-Route\] Saving outputs..."
write_def $::env(OUTPUT_DIR)/${design_name}_routed.def
write_db $::env(OUTPUT_DIR)/${design_name}_routed.odb

# DRC gate: fail the flow if any violations are present
if {[file exists $drc_rpt]} {
    set fp [open $drc_rpt r]
    set drc_txt [read $fp]
    close $fp
    set vio_count [regexp -all -line {^violation type:} $drc_txt]
    puts "\[Generic-Route\] DRC violations: $vio_count (report: $drc_rpt)"
    if {$vio_count > 0} {
        puts stderr "\[Generic-Route\] ERROR: DRC violations detected ($vio_count)."
        exit 2
    }
} else {
    puts stderr "\[Generic-Route\] ERROR: DRC report not found: $drc_rpt"
    exit 2
}

puts "\[Generic-Route\] Completed."
