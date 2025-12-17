# view_design.tcl - Open OpenROAD GUI to view an existing routed design
# Usage: openroad -gui view_design.tcl

# Check for required environment variables
set required_vars {DESIGN_NAME}
foreach var $required_vars {
    if {![info exists ::env($var)]} {
        puts "Error: Environment variable $var is not set."
        exit 1
    }
}

set design_name $::env(DESIGN_NAME)

# Optional file paths (will try to find them if not set)
set merged_lef ""
set lib_files ""
set verilog_file ""
set def_file ""
set odb_file ""

if {[info exists ::env(MERGED_LEF)]} {
    set merged_lef $::env(MERGED_LEF)
}
if {[info exists ::env(LIB_FILES)]} {
    set lib_files $::env(LIB_FILES)
}
if {[info exists ::env(VERILOG_FILE)]} {
    set verilog_file $::env(VERILOG_FILE)
}
if {[info exists ::env(DEF_FILE)]} {
    set def_file $::env(DEF_FILE)
}
if {[info exists ::env(ODB_FILE)]} {
    set odb_file $::env(ODB_FILE)
}

# Try to find files if not provided
set output_dir "build/$design_name"
if {[info exists ::env(OUTPUT_DIR)]} {
    set output_dir $::env(OUTPUT_DIR)
}

# Try to find routed DEF if DEF_FILE not set
if {$def_file == ""} {
    set routed_def "$output_dir/${design_name}_routed.def"
    if {[file exists $routed_def]} {
        set def_file $routed_def
        puts "Found routed DEF: $def_file"
    } else {
        set fixed_def "$output_dir/${design_name}_fixed.def"
        if {[file exists $fixed_def]} {
            set def_file $fixed_def
            puts "Found fixed DEF: $def_file"
        }
    }
}

# Try to find ODB file
if {$odb_file == ""} {
    set routed_odb "$output_dir/${design_name}_routed.odb"
    if {[file exists $routed_odb]} {
        set odb_file $routed_odb
        puts "Found routed ODB: $odb_file"
    }
}

# Try to find Verilog if not set
if {$verilog_file == ""} {
    set renamed_v "$output_dir/${design_name}_renamed.v"
    if {[file exists $renamed_v]} {
        set verilog_file $renamed_v
        puts "Found Verilog: $verilog_file"
    } else {
        set final_v "$output_dir/${design_name}_final.v"
        if {[file exists $final_v]} {
            set verilog_file $final_v
            puts "Found Verilog: $final_v"
        }
    }
}

# Try to find LEF if not set
if {$merged_lef == ""} {
    set default_lef "inputs/Platform/sky130_fd_sc_hd.merged.lef"
    if {[file exists $default_lef]} {
        set merged_lef $default_lef
        puts "Found LEF: $merged_lef"
    }
}

# Try to find Liberty if not set
if {$lib_files == ""} {
    set default_lib "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
    if {[file exists $default_lib]} {
        set lib_files $default_lib
        puts "Found Liberty: $lib_files"
    }
}

# Try to find RCX rules file if not set
set rcx_rules_file ""
if {[info exists ::env(RCX_RULES_FILE)]} {
    set rcx_rules_file $::env(RCX_RULES_FILE)
} else {
    # Auto-detect common RCX file locations
    set rcx_candidates {
        "inputs/Platform/rcx_patterns.rules"
        "inputs/Platform/sky130_rcx_rules"
        "inputs/Platform/sky130hd.rcx"
        "inputs/Platform/rcx_rules"
    }
    foreach candidate $rcx_candidates {
        if {[file exists $candidate]} {
            set rcx_rules_file $candidate
            puts "Found RCX rules file: $rcx_rules_file"
            break
        }
    }
}

puts "\[View-Design\] Loading design for viewing..."
puts "  Design: $design_name"
puts "  LEF: $merged_lef"
puts "  Liberty: $lib_files"
puts "  Verilog: $verilog_file"
puts "  DEF: $def_file"
puts "  ODB: $odb_file"
if {$rcx_rules_file != ""} {
    puts "  RCX Rules: $rcx_rules_file"
} else {
    puts "  RCX Rules: (not found - will try extraction without RCX)"
}

# Method 1: Try to load from ODB (fastest, if available)
if {$odb_file != "" && [file exists $odb_file]} {
    puts "\[View-Design\] Loading from ODB file..."
    
    # Load Liberty files first (required for some GUI features like heatmap)
    if {$lib_files != ""} {
        puts "  Loading Liberty files for GUI features..."
        foreach lib [split $lib_files " "] {
            if {[file exists $lib]} {
                puts "    Reading Liberty: $lib"
                read_liberty $lib
            }
        }
    } else {
        # Try to find default Liberty file
        set default_lib "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"
        if {[file exists $default_lib]} {
            puts "  Loading default Liberty: $default_lib"
            read_liberty $default_lib
        }
    }
    
    # Now load the ODB file
    read_db $odb_file
    puts "\[View-Design\] Design loaded from ODB."
    
    # Check if SPEF extraction is requested or if SPEF file doesn't exist
    set extract_spef 0
    set spef_file "$output_dir/${design_name}.spef"
    
    # Check if EXTRACT_SPEF environment variable is set
    if {[info exists ::env(EXTRACT_SPEF)] && $::env(EXTRACT_SPEF) == "1"} {
        set extract_spef 1
        puts "\[View-Design\] EXTRACT_SPEF=1, will extract SPEF file"
    } elseif {![file exists $spef_file]} {
        # Auto-extract if SPEF doesn't exist
        set extract_spef 1
        puts "\[View-Design\] SPEF file not found, will extract: $spef_file"
    }
    
    # Extract SPEF if needed
    if {$extract_spef} {
        puts "\[View-Design\] Extracting parasitics and generating SPEF..."
        
        set extraction_success 0
        
        # Method 1: Try with RCX rules file (auto-detected or explicitly provided)
        if {$rcx_rules_file != "" && [file exists $rcx_rules_file]} {
            puts "\[View-Design\] Attempting extraction with RCX rules file: $rcx_rules_file"
            if {[catch {
                extract_parasitics -ext_model_file $rcx_rules_file
                puts "\[View-Design\] Parasitic extraction completed (with RCX file)"
                set extraction_success 1
            } error_msg]} {
                puts "\[View-Design\] Warning: extract_parasitics with RCX file failed: $error_msg"
                puts "\[View-Design\] Trying without RCX file..."
            }
        } elseif {[info exists ::env(RCX_RULES_FILE)] && [file exists $::env(RCX_RULES_FILE)]} {
            # Fallback to environment variable if auto-detection didn't find it
            puts "\[View-Design\] Attempting extraction with RCX rules file from env: $::env(RCX_RULES_FILE)"
            if {[catch {
                extract_parasitics -ext_model_file $::env(RCX_RULES_FILE)
                puts "\[View-Design\] Parasitic extraction completed (with RCX file)"
                set extraction_success 1
            } error_msg]} {
                puts "\[View-Design\] Warning: extract_parasitics with RCX file failed: $error_msg"
                puts "\[View-Design\] Trying without RCX file..."
            }
        }
        
        # Method 2: Try without RCX file (use default tech info)
        if {!$extraction_success} {
            puts "\[View-Design\] Attempting extraction without RCX file (using default tech info)..."
            if {[catch {
                # Try extract_parasitics without any arguments
                extract_parasitics
                puts "\[View-Design\] Parasitic extraction completed (using default tech info)"
                set extraction_success 1
            } error_msg]} {
                puts "\[View-Design\] Error: extract_parasitics failed: $error_msg"
                puts "\[View-Design\] This may be due to missing RCX rules or incompatible OpenROAD version"
                puts "\[View-Design\] SPEF file will not be generated"
                puts "\[View-Design\] Note: You can use scripts/generate_spef.py as an alternative"
                set extract_spef 0
            }
        }
        
        # Write SPEF file if extraction was successful
        if {$extract_spef && $extraction_success} {
            if {[catch {
                write_spef $spef_file
                puts "\[View-Design\] SPEF file written: $spef_file"
            } error_msg]} {
                puts "\[View-Design\] Warning: Failed to write SPEF file: $error_msg"
            }
        }
    } else {
        puts "\[View-Design\] SPEF file already exists: $spef_file"
        puts "\[View-Design\] To re-extract, set EXTRACT_SPEF=1 environment variable"
    }
    
    puts "\[View-Design\] Opening GUI..."
    # GUI will open automatically when script is run with -gui flag
    return
}

# Method 2: Load from DEF + Verilog + LEF
if {$def_file != "" && [file exists $def_file] && $verilog_file != "" && [file exists $verilog_file]} {
    puts "\[View-Design\] Loading design from DEF + Verilog..."
    
    # Read LEF files
    if {$merged_lef != "" && [file exists $merged_lef]} {
        puts "  Reading LEF: $merged_lef"
        read_lef $merged_lef
    }
    
    # Read Liberty files
    if {$lib_files != ""} {
        foreach lib [split $lib_files " "] {
            if {[file exists $lib]} {
                puts "  Reading Liberty: $lib"
                read_liberty $lib
            }
        }
    }
    
    # Read Verilog
    if {[file exists $verilog_file]} {
        puts "  Reading Verilog: $verilog_file"
        read_verilog $verilog_file
    }
    
    # Link design
    puts "  Linking design: $design_name"
    link_design $design_name
    
    # Read DEF
    if {[file exists $def_file]} {
        puts "  Reading DEF: $def_file"
        read_def $def_file
    }
    
    puts "\[View-Design\] Design loaded."
    
    # Check if SPEF extraction is requested or if SPEF file doesn't exist
    set extract_spef 0
    set spef_file "$output_dir/${design_name}.spef"
    
    # Check if EXTRACT_SPEF environment variable is set
    if {[info exists ::env(EXTRACT_SPEF)] && $::env(EXTRACT_SPEF) == "1"} {
        set extract_spef 1
        puts "\[View-Design\] EXTRACT_SPEF=1, will extract SPEF file"
    } elseif {![file exists $spef_file]} {
        # Auto-extract if SPEF doesn't exist
        set extract_spef 1
        puts "\[View-Design\] SPEF file not found, will extract: $spef_file"
    }
    
    # Extract SPEF if needed
    if {$extract_spef} {
        puts "\[View-Design\] Extracting parasitics and generating SPEF..."
        
        set extraction_success 0
        
        # Method 1: Try with RCX rules file (auto-detected or explicitly provided)
        if {$rcx_rules_file != "" && [file exists $rcx_rules_file]} {
            puts "\[View-Design\] Attempting extraction with RCX rules file: $rcx_rules_file"
            if {[catch {
                extract_parasitics -ext_model_file $rcx_rules_file
                puts "\[View-Design\] Parasitic extraction completed (with RCX file)"
                set extraction_success 1
            } error_msg]} {
                puts "\[View-Design\] Warning: extract_parasitics with RCX file failed: $error_msg"
                puts "\[View-Design\] Trying without RCX file..."
            }
        } elseif {[info exists ::env(RCX_RULES_FILE)] && [file exists $::env(RCX_RULES_FILE)]} {
            # Fallback to environment variable if auto-detection didn't find it
            puts "\[View-Design\] Attempting extraction with RCX rules file from env: $::env(RCX_RULES_FILE)"
            if {[catch {
                extract_parasitics -ext_model_file $::env(RCX_RULES_FILE)
                puts "\[View-Design\] Parasitic extraction completed (with RCX file)"
                set extraction_success 1
            } error_msg]} {
                puts "\[View-Design\] Warning: extract_parasitics with RCX file failed: $error_msg"
                puts "\[View-Design\] Trying without RCX file..."
            }
        }
        
        # Method 2: Try without RCX file (use default tech info)
        if {!$extraction_success} {
            puts "\[View-Design\] Attempting extraction without RCX file (using default tech info)..."
            if {[catch {
                # Try extract_parasitics without any arguments
                extract_parasitics
                puts "\[View-Design\] Parasitic extraction completed (using default tech info)"
                set extraction_success 1
            } error_msg]} {
                puts "\[View-Design\] Error: extract_parasitics failed: $error_msg"
                puts "\[View-Design\] This may be due to missing RCX rules or incompatible OpenROAD version"
                puts "\[View-Design\] SPEF file will not be generated"
                puts "\[View-Design\] Note: You can use scripts/generate_spef.py as an alternative"
                set extract_spef 0
            }
        }
        
        # Write SPEF file if extraction was successful
        if {$extract_spef && $extraction_success} {
            if {[catch {
                write_spef $spef_file
                puts "\[View-Design\] SPEF file written: $spef_file"
            } error_msg]} {
                puts "\[View-Design\] Warning: Failed to write SPEF file: $error_msg"
            }
        }
    } else {
        puts "\[View-Design\] SPEF file already exists: $spef_file"
        puts "\[View-Design\] To re-extract, set EXTRACT_SPEF=1 environment variable"
    }
    
    puts "\[View-Design\] Opening GUI..."
    # GUI will open automatically when script is run with -gui flag
    return
}

# If we get here, we couldn't load the design
puts "\[View-Design\] ERROR: Could not find required files to load design."
puts "\[View-Design\] Please ensure at least one of the following exists:"
puts "  - ODB file: $output_dir/${design_name}_routed.odb"
puts "  - DEF + Verilog: $output_dir/${design_name}_routed.def + $output_dir/${design_name}_renamed.v"
puts ""
puts "\[View-Design\] You can also set environment variables:"
puts "  - ODB_FILE: path to .odb file"
puts "  - DEF_FILE: path to .def file"
puts "  - VERILOG_FILE: path to .v file"
puts "  - MERGED_LEF: path to merged LEF file"
puts "  - LIB_FILES: path to Liberty file(s)"

