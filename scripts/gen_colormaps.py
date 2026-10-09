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

# cmocean does not expose a type, so this follows its documentation (topo is listed as diverging).
CMOCEAN_TYPES = {
    "Sequential": [
        "thermal", "haline", "solar", "ice", "gray", "oxy", "deep", "dense", "algae", "matter",
        "turbid", "speed", "amp", "tempo", "rain",
    ],
    "Diverging": ["balance", "delta", "curl", "diff", "tarn", "topo"],
    "Cyclic": ["phase"],
}  # fmt: skip
TYPE_ORDER = ["Sequential", "Diverging", "Cyclic"]  # any other type follows, qualitative is omitted


def sample(cmap_name: str) -> list[list[int]]:
    rgba = matplotlib.colormaps[cmap_name](np.linspace(0, 1, N_STOPS))
    return np.round(rgba[:, :3] * 255).astype(int).tolist()


def type_groups(source: str, prefix: str, types: dict[str, list[str]]) -> list[dict]:
    """One group per colourmap type, labelled ``<source>: <Type>``, in TYPE_ORDER."""
    order = sorted(
        types, key=lambda t: (TYPE_ORDER.index(t) if t in TYPE_ORDER else len(TYPE_ORDER), t)
    )
    return [
        {"label": f"{source}: {t}", "source": source, "names": [f"{prefix}{n}" for n in types[t]]}
        for t in order
        if t != "Qualitative"
    ]


def main() -> None:
    groups = [
        {"label": label, "source": "core", "names": names} for label, names in CORE_GROUPS.items()
    ]
    cmo = list(cmocean.cm.cmapnames)
    flat = [n for names in CMOCEAN_TYPES.values() for n in names]
    assert sorted(flat) == sorted(cmo), "CMOCEAN_TYPES must cover exactly cmocean.cm.cmapnames"
    groups += type_groups("cmocean", "cmo.", CMOCEAN_TYPES)
    cmr_types: dict[str, list[str]] = {}
    for n in cmasher.get_cmap_list():
        if n.endswith("_r"):
            continue
        base = n[:-2] if n.endswith("_s") else n  # the API does not handle the _s variants
        cmr_types.setdefault(cmasher.get_cmap_type(base).capitalize(), []).append(n)
    groups += type_groups("cmasher", "cmr.", cmr_types)
    colormaps = {n: sample(n) for g in groups for n in g["names"]}
    OUT.write_text(json.dumps({"groups": groups, "colormaps": colormaps}, separators=(",", ":")))
    print(f"wrote {OUT} ({len(colormaps)} colourmaps, {len(groups)} groups)")
    for g in groups:
        print(f"  {g['label']}: {len(g['names'])}")


if __name__ == "__main__":
    main()
