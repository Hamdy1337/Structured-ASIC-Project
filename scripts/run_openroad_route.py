import argparse
import os
import subprocess
from pathlib import Path


def count_drc_violations(drc_report: Path) -> int:
    if not drc_report.exists():
        return -1
    count = 0
    with drc_report.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("violation type:"):
                count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OpenROAD routing and fail if DRC violations exist.")
    parser.add_argument("design", help="Design name (e.g., 6502, arith, aes_128)")
    parser.add_argument(
        "--openroad",
        default="openroad",
        help="OpenROAD executable (default: openroad on PATH)",
    )
    parser.add_argument(
        "--route_tcl",
        default=str(Path("src") / "routing" / "route.tcl"),
        help="Path to route.tcl",
    )
    parser.add_argument(
        "--build_dir",
        default=str(Path("build")),
        help="Build directory root (default: build)",
    )
    parser.add_argument(
        "--lef_files",
        default=f"{Path('inputs') / 'Platform' / 'sky130_fd_sc_hd.tlef'} {Path('inputs') / 'Platform' / 'sky130_fd_sc_hd.lef'}",
        help="Space-separated LEF files (default: sky130 tlef + lef)",
    )
    parser.add_argument(
        "--lib_files",
        default="",
        help="Space-separated Liberty files list",
    )
    parser.add_argument(
        "--verilog",
        default=None,
        help="Verilog to use for routing connectivity (default: build/<design>/<design>_renamed.v)",
    )
    parser.add_argument(
        "--def_file",
        dest="def_file",
        default=None,
        help="DEF placement file (default: build/<design>/<design>_fixed.def)",
    )

    args = parser.parse_args()

    design = args.design
    build_dir = Path(args.build_dir) / design
    build_dir.mkdir(parents=True, exist_ok=True)

    verilog_file = Path(args.verilog) if args.verilog else (build_dir / f"{design}_renamed.v")
    def_file = Path(args.def_file) if args.def_file else (build_dir / f"{design}_fixed.def")
    route_tcl = Path(args.route_tcl)

    env = os.environ.copy()
    env["DESIGN_NAME"] = design
    env["LEF_FILES"] = args.lef_files
    merged_candidate = Path("inputs") / "Platform" / "sky130_fd_sc_hd_merged.lef"
    if merged_candidate.exists():
        env["MERGED_LEF"] = str(merged_candidate)
    env["LIB_FILES"] = args.lib_files
    env["VERILOG_FILE"] = str(verilog_file)
    env["DEF_FILE"] = str(def_file)
    env["OUTPUT_DIR"] = str(build_dir)

    drc_rpt = build_dir / f"{design}_drc.rpt"

    cmd = [args.openroad, "-exit", str(route_tcl)]
    print("Running:")
    print(" ", " ".join(cmd))
    print("OUTPUT_DIR:", build_dir)

    try:
        proc = subprocess.run(cmd, env=env, check=False)
    except FileNotFoundError:
        print(f"ERROR: OpenROAD executable not found: {args.openroad}")
        return 127

    vio = count_drc_violations(drc_rpt)
    if vio == -1:
        print(f"ERROR: DRC report not found: {drc_rpt}")
        return 2

    print(f"DRC violations: {vio}")

    # route.tcl already exits non-zero when vio>0, but keep this as a safety net.
    if vio > 0:
        return 2

    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
