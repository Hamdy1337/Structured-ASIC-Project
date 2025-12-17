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

# Optional OUTPUT_SUFFIX for distinguishing RL from SA output files (e.g., "_rl")
if {[info exists ::env(OUTPUT_SUFFIX)]} {
    set output_suffix $::env(OUTPUT_SUFFIX)
} else {
    set output_suffix ""
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
# Read Def (Apply placement to the linked design)
# 1. Initialize Floorplan (Die Area, etc.) using separate FP file to avoid pin duplication
# Read Def (Apply placement to the linked design)
# 1. Initialize Floorplan (Die Area) via generated TCL script to avoid ODB errors
set fp_tcl [string map {".def" "_fp.tcl"} $::env(DEF_FILE)]
if {[file exists $fp_tcl]} {
    puts "Sourcing Floorplan from $fp_tcl..."
    source $fp_tcl
} else {
    puts "Error: Floorplan TCL $fp_tcl not found!"
    exit 1
}

# 2. Add Components (Incremental)
read_def -incremental $::env(DEF_FILE)

puts "\[Debug\] Instance Count after DEF load: [llength [get_cells *]]"

make_tracks li1 -x_offset 0.24 -x_pitch 0.48 -y_offset 0.185 -y_pitch 0.37
make_tracks met1 -x_offset 0.185 -x_pitch 0.37 -y_offset 0.185 -y_pitch 0.37
make_tracks met2 -x_offset 0.24 -x_pitch 0.48 -y_offset 0.24 -y_pitch 0.48
make_tracks met3 -x_offset 0.37 -x_pitch 0.74 -y_offset 0.37 -y_pitch 0.74
make_tracks met4 -x_offset 0.48 -x_pitch 0.96 -y_offset 0.48 -y_pitch 0.96
make_tracks met5 -x_offset 1.85 -x_pitch 3.33 -y_offset 1.85 -y_pitch 3.33

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
set drc_rpt $::env(OUTPUT_DIR)/${design_name}${output_suffix}_drc.rpt
set_routing_layers -signal met1-met5 -clock met1-met5
detailed_route \
               -output_drc $drc_rpt \
               -output_maze $::env(OUTPUT_DIR)/${design_name}${output_suffix}_maze.log \
               -output_guide $::env(OUTPUT_DIR)/${design_name}${output_suffix}.guide \
               -droute_end_iter 64

# 4. Extract Parasitics
puts "\[Generic-Route\] Extracting parasitics..."
set spef_file $::env(OUTPUT_DIR)/${design_name}${output_suffix}.spef

# Auto-detect RCX rules file if not provided
set rcx_rules_file ""
if {[info exists ::env(RCX_RULES_FILE)] && [file exists $::env(RCX_RULES_FILE)]} {
    set rcx_rules_file $::env(RCX_RULES_FILE)
} else {
    # Try to find RCX file in common locations
    set rcx_candidates {
        "inputs/Platform/rcx_patterns.rules"
        "inputs/Platform/sky130_rcx_rules"
        "inputs/Platform/sky130hd.rcx"
        "inputs/Platform/rcx_rules"
    }
    foreach candidate $rcx_candidates {
        if {[file exists $candidate]} {
            set rcx_rules_file $candidate
            puts "\[Generic-Route\] Auto-detected RCX rules file: $rcx_rules_file"
            break
        }
    }
}

if {$rcx_rules_file != "" && [file exists $rcx_rules_file]} {
    puts "\[Generic-Route\] Using RCX rules file: $rcx_rules_file"
    if {[catch {extract_parasitics -ext_model_file $rcx_rules_file} error_msg]} {
        puts "\[Generic-Route\] Warning: extract_parasitics with RCX file failed: $error_msg"
        puts "\[Generic-Route\] Attempting extract_parasitics without RCX file..."
        if {[catch {extract_parasitics} error_msg2]} {
            puts "\[Generic-Route\] Error: extract_parasitics failed: $error_msg2"
            puts "\[Generic-Route\] Continuing without parasitic extraction..."
        } else {
            puts "\[Generic-Route\] Parasitic extraction completed (without RCX file)"
        }
    } else {
        puts "\[Generic-Route\] Parasitic extraction completed (with RCX file)"
    }
} else {
    puts "\[Generic-Route\] RCX rules file not found. Running extract_parasitics with default tech info..."
    puts "\[Generic-Route\] (To use RCX file, place it at: inputs/Platform/rcx_patterns.rules)"
    if {[catch {extract_parasitics} error_msg]} {
        puts "\[Generic-Route\] Warning: extract_parasitics failed: $error_msg"
        puts "\[Generic-Route\] Continuing without parasitic extraction..."
    } else {
        puts "\[Generic-Route\] Parasitic extraction completed (using default tech info)"
    }
}

# Write SPEF file if extraction was successful
if {[catch {write_spef $spef_file} error_msg]} {
    puts "\[Generic-Route\] Warning: Failed to write SPEF file: $error_msg"
    puts "\[Generic-Route\] SPEF file may not be available for STA"
} else {
    puts "\[Generic-Route\] SPEF file written: $spef_file"
}

# 5. Congestion report
# Note: report_congestion command was removed in newer OpenROAD versions
# Congestion data is now available in the GUI or via global_route -congestion_report_file
# The congestion report file should have been generated during global_route above
if {[file exists $congestion_rpt]} {
    puts "\[Generic-Route\] Congestion report available: $congestion_rpt"
    puts "\[Generic-Route\] To view congestion heatmap, use OpenROAD GUI:"
    puts "\[Generic-Route\]   1. Load design: openroad -gui"
    puts "\[Generic-Route\]   2. View → Heat Maps → Routing Congestion"
} else {
    puts "\[Generic-Route\] NOTE: Congestion report not generated."
    puts "\[Generic-Route\] Congestion data is available in the routing database (ODB file)."
    puts "\[Generic-Route\] View it in GUI: View → Heat Maps → Routing Congestion"
}

# Save Outputs
puts "\[Generic-Route\] Saving outputs..."
write_def $::env(OUTPUT_DIR)/${design_name}${output_suffix}_routed.def
write_db $::env(OUTPUT_DIR)/${design_name}${output_suffix}_routed.odb

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

