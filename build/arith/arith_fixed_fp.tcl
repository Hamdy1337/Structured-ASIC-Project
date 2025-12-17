# Floorplan Initialization Script
catch {link_design arith}
initialize_floorplan -site unithd -die_area "0 0 1003.6 989.2" -core_area "0 0 1003.6 989.2"
puts "\[Floorplan\] Die/Core Area set via initialize_floorplan: 0 0 1003.6 989.2"
