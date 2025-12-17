"""make_def.py

Project-spec entrypoint for generating a fixed DEF for routing.

This is a thin wrapper around scripts/generate_def.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
	p = argparse.ArgumentParser(description="Generate build/<design>/<design>_fixed.def")
	p.add_argument("design", help="Design name (e.g., 6502)")
	p.add_argument("--fabric_cells", default="inputs/Platform/fabric_cells.yaml")
	p.add_argument("--pins", default="inputs/Platform/pins.yaml")
	p.add_argument(
		"--map",
		dest="map_path",
		default=None,
		help="Placement map file (default: build/<design>/<design>.map)",
	)
	p.add_argument("--fabric_def", default="inputs/Platform/fabric.yaml")
	p.add_argument(
		"--output",
		default=None,
		help="Output DEF file (default: build/<design>/<design>_fixed.def)",
	)
	return p.parse_args()


def main() -> int:
	args = parse_args()
	scripts_dir = Path(__file__).resolve().parent
	if str(scripts_dir) not in sys.path:
		sys.path.insert(0, str(scripts_dir))

	from generate_def import generate_def

	class Args:
		pass

	a = Args()
	a.design_name = args.design
	a.fabric_cells = args.fabric_cells
	a.pins = args.pins
	a.map = args.map_path or str(Path("build") / args.design / f"{args.design}.map")
	a.fabric_def = args.fabric_def
	a.output = args.output or str(Path("build") / args.design / f"{args.design}_fixed.def")

	generate_def(a)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

