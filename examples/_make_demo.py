"""Generate examples/demo.ipynb (no outputs). Run: python _make_demo.py"""

from pathlib import Path

import nbformat as nbf
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

cells = [
    new_markdown_cell(
        "# GeoZarr pyramid demo\n\n"
        "Convert a slice of the OceanSODA-ETHZ-HR delta-fCO2 product (global, 0.25 degrees, "
        "Zarr v3) into a GeoZarr multiscale pyramid, validate it and look at two levels."
    ),
    new_code_cell(
        "import matplotlib.pyplot as plt\n"
        "import xarray as xr\n\n"
        "import geozarr_pyramid_maker as gpm"
    ),
    new_code_cell('gpm.configure_logging("INFO")'),
    new_markdown_cell(
        "## Open the data\n\n"
        "The full store has 1886 weekly steps. We take only the first 12 (about 47 MiB) so "
        "the demo runs in seconds. The source has no CRS information, so EPSG:4326 is "
        "inferred (with a warning) and the ascending latitude axis is flipped north-up "
        "(also with a warning)."
    ),
    new_code_cell(
        'URL = "https://s3.waw4-1.cloudferro.com/EarthCODE/OSCAssets/ocean-soda/dfco2.zarr/"\n'
        "ds = xr.open_zarr(URL).isel(time=slice(0, 12))\n"
        "ds"
    ),
    new_code_cell("plan = ds.geozarr.plan()\nprint(plan)"),
    new_code_cell(
        'result = ds.geozarr.to_pyramid("oceansoda_dfco2.zarr", overwrite=True)\n'
        "print(f\"total: {result.timings['total']:.1f} s\")"
    ),
    new_code_cell("print(result.validation)\ngpm.is_valid(result.validation)"),
    new_code_cell(
        'tree = xr.open_datatree("oceansoda_dfco2.zarr", engine="zarr")\n'
        "for name, node in tree.children.items():\n"
        '    print(name, dict(node.ds.sizes), node.ds["dfco2"].encoding.get("shards"))'
    ),
    new_code_cell(
        "fig, axes = plt.subplots(1, 2, figsize=(12, 3.5), constrained_layout=True)\n"
        'for ax, name in zip(axes, ("0", str(len(plan.levels) - 1)), strict=True):\n'
        '    da = tree[name].ds["dfco2"].isel(time=0)\n'
        "    x, y = da[da.dims[-1]].values, da[da.dims[-2]].values\n"
        "    dx, dy = abs(x[1] - x[0]), abs(y[1] - y[0])\n"
        "    extent = (x[0] - dx / 2, x[-1] + dx / 2, y[-1] - dy / 2, y[0] + dy / 2)\n"
        '    im = ax.imshow(da.values, extent=extent, cmap="RdBu_r", vmin=-50, vmax=50)\n'
        '    ax.set_title(f"level {name}: {da.shape[0]} x {da.shape[1]}")\n'
        'fig.colorbar(im, ax=axes, label="dfco2 (uatm)", shrink=0.8)\n'
        "plt.show()"
    ),
    new_markdown_cell(
        "## Preview in the browser\n\n"
        "Run this in a terminal (the server blocks, so it is not a notebook cell):\n\n"
        "```bash\ngeozarr-pyramid preview oceansoda_dfco2.zarr --serve\n```"
    ),
]

nb = new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
nbf.write(nb, Path(__file__).with_name("demo.ipynb"))
