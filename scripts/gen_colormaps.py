"""Regenerate ``src/geozarr_pyramid_maker/colormaps.json`` (dev-time only, D-42).

Needs matplotlib, cmocean and cmasher (dev dependencies). The package itself reads the JSON and has
no runtime dependency on them. Run with ``.venv/bin/python scripts/gen_colormaps.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import cmasher
import cmocean
import matplotlib
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "src/geozarr_pyramid_maker/colormaps.json"
N_STOPS = 11  # sampled at 0, 0.1 ... 1

# Matplotlib's colormap reference groups (Qualitative is left out: discrete maps do not suit a
# continuous scale). RdBu_r is a legacy name kept from D-22.
CORE_GROUPS = {
    "Perceptually uniform sequential": ["viridis", "plasma", "inferno", "magma", "cividis"],
    "Sequential": [
        "Greys", "Purples", "Blues", "Greens", "Oranges", "Reds", "YlOrBr", "YlOrRd", "OrRd",
        "PuRd", "RdPu", "BuPu", "GnBu", "PuBu", "YlGnBu", "PuBuGn", "BuGn", "YlGn",
    ],
    "Sequential (2)": [
        "binary", "gist_yarg", "gist_gray", "gray", "bone", "pink", "spring", "summer", "autumn",
        "winter", "cool", "Wistia", "hot", "afmhot", "gist_heat", "copper",
    ],
    "Diverging": [
        "PiYG", "PRGn", "BrBG", "PuOr", "RdGy", "RdBu", "RdBu_r", "RdYlBu", "RdYlGn", "Spectral",
        "coolwarm", "bwr", "seismic", "berlin", "managua", "vanimo",
    ],
    "Cyclic": ["twilight", "twilight_shifted", "hsv"],
    "Miscellaneous": [
        "flag", "prism", "ocean", "gist_earth", "terrain", "gist_stern", "gnuplot", "gnuplot2",
        "CMRmap", "cubehelix", "brg", "gist_rainbow", "rainbow", "jet", "turbo", "nipy_spectral",
        "gist_ncar",
    ],
}  # fmt: skip


def sample(cmap_name: str) -> list[list[int]]:
    rgba = matplotlib.colormaps[cmap_name](np.linspace(0, 1, N_STOPS))
    return np.round(rgba[:, :3] * 255).astype(int).tolist()


def main() -> None:
    groups = [
        {"label": label, "source": "core", "names": names} for label, names in CORE_GROUPS.items()
    ]
    cmo = [n for n in cmocean.cm.cmapnames]
    cmr = [n for n in cmasher.get_cmap_list() if not n.endswith("_r")]
    groups.append({"label": "cmocean", "source": "cmocean", "names": [f"cmo.{n}" for n in cmo]})
    groups.append({"label": "cmasher", "source": "cmasher", "names": [f"cmr.{n}" for n in cmr]})
    colormaps = {n: sample(n) for g in groups for n in g["names"]}
    OUT.write_text(json.dumps({"groups": groups, "colormaps": colormaps}, separators=(",", ":")))
    print(f"wrote {OUT} ({len(colormaps)} colourmaps, {len(groups)} groups)")


if __name__ == "__main__":
    main()
