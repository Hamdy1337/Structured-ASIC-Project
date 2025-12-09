# scripts/view_congestion.tcl
# Usage: openroad -gui -init scripts/view_congestion.tcl

puts "Reading Liberty (Timing) files..."
if { [file exists "inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib"] } {
    read_liberty inputs/Platform/sky130_fd_sc_hd__tt_025C_1v80.lib
} else {
    puts "Error: Liberty file not found!"
}

puts "Reading Failed Route Database..."
if { [file exists "build/z80/z80_failed_route.odb"] } {
    read_db build/z80/z80_failed_route.odb
} else {
    puts "Error: ODB file build/z80/z80_failed_route.odb not found."
    puts "Make sure you have run the failed routing step first."
}

puts "Database and Libraries loaded."
puts "You can now inspect congestion in the GUI."
