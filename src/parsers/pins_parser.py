from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Tuple, cast

import yaml
import numpy as np
import pandas as pd

TOL: float = 1e-4  # micron tolerance for edge/track checks
SIDES = ("south", "north", "west", "east")


def _require(d: Dict[str, Any], key: str, ctx: str = "") -> Any:
    if key not in d:
        raise ValueError(f"Missing key '{key}' {('in ' + ctx) if ctx else ''}")
    return d[key]


def _track_index(start_um: float, step_um: float, coord_um: float) -> int:
    """Return integer track index if aligned within tolerance, else raise."""
    if step_um <= 0:
        raise ValueError("Track step must be > 0")
    idx = (coord_um - start_um) / step_um
    ridx = int(np.rint(idx))
    if abs((idx - ridx) * step_um) <= TOL:
        return ridx
    raise ValueError(
        f"Coordinate {coord_um:.6f} µm not on track grid "
        f"(start={start_um}, step={step_um}); computed idx={idx:.6f}"
    )


@dataclass(frozen=True)
class Units:
    coords: str
    dbu_per_micron: int


@dataclass(frozen=True)
class Track:
    start_um: float
    step_um: float


@dataclass(frozen=True)
class Die:
    width_um: float
    height_um: float
    core_margin_um: float
    corner_keepout_um: float


@dataclass(frozen=True)
class Core:
    width_um: float
    height_um: float


@dataclass(frozen=True)
class PinsMeta:
    version: str
    units: Units
    layers: Dict[str, str]
    tracks: Dict[str, Track]
    die: Die
    core: Core
    groups_per_side: int
    pin_spacing_tracks: int
    pin_spacing_um: Dict[str, float]


def load_and_validate(path: str) -> Tuple[pd.DataFrame, PinsMeta]:
    with open(path, "r") as f:
        root = yaml.safe_load(f)

    if not isinstance(root, dict) or "pin_placement" not in root:
        raise ValueError("Top-level key 'pin_placement' not found")
    if not isinstance(root["pin_placement"], dict):
        raise ValueError("'pin_placement' must be a mapping")
    pp: Dict[str, Any] = cast(Dict[str, Any], root["pin_placement"])

    # Units
    units = cast(Dict[str, Any], _require(pp, "units", "pin_placement"))
    coords = _require(units, "coords", "units")
    if coords != "microns":
        raise ValueError("units.coords must be 'microns'")
    dbu_per_micron = _require(units, "dbu_per_micron", "units")
    if not isinstance(dbu_per_micron, int) or dbu_per_micron <= 0:
        raise ValueError("units.dbu_per_micron must be positive integer")
    units_meta = Units(coords=str(coords), dbu_per_micron=int(dbu_per_micron))

    # Layers by side
    layers = cast(Dict[str, Any], _require(pp, "layers", "pin_placement"))
    for s in SIDES:
        if s not in layers:
            raise ValueError(f"layers.{s} missing")
    layers_by_side: Dict[str, str] = {s: str(layers[s]) for s in SIDES}

    # Tracks per metal
    tracks_raw = cast(Dict[str, Any], _require(pp, "tracks", "pin_placement"))
    if not tracks_raw:
        raise ValueError("tracks must be a non-empty mapping")
    tracks: Dict[str, Track] = {}
    for metal, tinfo in tracks_raw.items():
        if not isinstance(tinfo, dict):
            raise ValueError(f"tracks.{metal} must be a mapping")
        tracks[str(metal)] = Track(
            start_um=float(_require(tinfo, "start_um", f"tracks.{metal}")),
            step_um=float(_require(tinfo, "step_um", f"tracks.{metal}")),
        )

    # Die & core
    die = cast(Dict[str, Any], _require(pp, "die", "pin_placement"))
    die_w: float = float(_require(die, "width_um", "die"))
    die_h: float = float(_require(die, "height_um", "die"))
    core_margin_um: float = float(die.get("core_margin_um", 0.0))
    corner_keepout_um: float = float(die.get("corner_keepout_um", 0.0))
    die_meta = Die(die_w, die_h, core_margin_um, corner_keepout_um)
    core = cast(Dict[str, Any], _require(pp, "core", "pin_placement"))
    core_w: float = float(_require(core, "width_um", "core"))
    core_h: float = float(_require(core, "height_um", "core"))
    core_meta = Core(core_w, core_h)
    if any(v <= 0 for v in (die_w, die_h, core_w, core_h)):
        raise ValueError("die/core width/height must be positive")

    # Spacing map exists for the metals used
    pin_spacing_um = cast(Dict[str, Any], _require(pp, "pin_spacing_um", "pin_placement"))
    for m in set(layers_by_side.values()):
        if m not in pin_spacing_um:
            raise ValueError(f"pin_spacing_um missing entry for metal '{m}'")
    pin_spacing_um_typed: Dict[str, float] = {str(k): float(v) for k, v in pin_spacing_um.items()}

    # Pins
    pins = cast(List[Any], _require(pp, "pins", "pin_placement"))

    # Validate pins & compute derived data
    out_rows: List[Dict[str, Any]] = []
    for i, p in enumerate(pins):
        if not isinstance(p, dict):
            raise ValueError(f"pin[{i}] must be a mapping")
        for req in ("name", "side", "layer", "x_um", "y_um", "direction", "status"):
            if req not in p:
                raise ValueError(f"pin[{i}] missing '{req}'")
        name = str(p["name"])
        side = str(p["side"]).lower()
        layer = str(p["layer"])
        x_um = float(p["x_um"])
        y_um = float(p["y_um"])
        direction = str(p["direction"]).upper()
        status = str(p["status"]).upper()

        if side not in SIDES:
            raise ValueError(f"pin '{name}': invalid side '{side}'")

        # Side-layer consistency
        expected_metal = layers_by_side[side]
        if layer != expected_metal:
            raise ValueError(
                f"pin '{name}': layer '{layer}' doesn't match side '{side}' metal '{expected_metal}'"
            )

        # Must lie on correct die edge + running coord
        if side == "south":
            if abs(y_um - 0.0) > TOL or not (-TOL <= x_um <= die_w + TOL):
                raise ValueError(f"pin '{name}': must lie on south edge (y=0)")
            run_coord = x_um
        elif side == "north":
            if abs(y_um - die_h) > TOL or not (-TOL <= x_um <= die_w + TOL):
                raise ValueError(f"pin '{name}': must lie on north edge (y=die_h)")
            run_coord = x_um
        elif side == "west":
            if abs(x_um - 0.0) > TOL or not (-TOL <= y_um <= die_h + TOL):
                raise ValueError(f"pin '{name}': must lie on west edge (x=0)")
            run_coord = y_um
        else:  # east
            if abs(x_um - die_w) > TOL or not (-TOL <= y_um <= die_h + TOL):
                raise ValueError(f"pin '{name}': must lie on east edge (x=die_w)")
            run_coord = y_um

        # Track alignment along running axis (layer’s grid)
        grid = tracks[layer]
        track_idx = _track_index(grid.start_um, grid.step_um, run_coord)

        # DBU
        x_dbu = int(np.rint(x_um * dbu_per_micron))
        y_dbu = int(np.rint(y_um * dbu_per_micron))

        out_rows.append(
            dict(
                name=name,
                side=side,
                layer=layer,
                x_um=x_um,
                y_um=y_um,
                x_dbu=x_dbu,
                y_dbu=y_dbu,
                track_idx=track_idx,
                direction=direction,
                status=status,
            )
        )

    df = pd.DataFrame(out_rows, columns=[
        "name", "side", "layer",
        "x_um", "y_um", "x_dbu", "y_dbu",
        "track_idx", "direction", "status",
    ])

    version = str(_require(pp, "version", "pin_placement")) if "version" in pp else ""
    groups_per_side = int(_require(pp, "groups_per_side", "pin_placement")) if "groups_per_side" in pp else 0
    pin_spacing_tracks = int(_require(pp, "pin_spacing_tracks", "pin_placement")) if "pin_spacing_tracks" in pp else 0

    meta = PinsMeta(
        version=version,
        units=units_meta,
        layers=layers_by_side,
        tracks=tracks,
        die=die_meta,
        core=core_meta,
        groups_per_side=groups_per_side,
        pin_spacing_tracks=pin_spacing_tracks,
        pin_spacing_um=pin_spacing_um_typed,
    )

    return df, meta

if __name__ == "__main__":
    pins_file_path = "inputs/Platform/pins.yaml"
    pins_df, pins_meta = load_and_validate(pins_file_path)
    print(pins_df.head())