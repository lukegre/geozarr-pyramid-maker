"""Build a GeoZarr pyramid from the first 12 time steps of OceanSODA-ETHZ-HR delta-fCO2.

python oceansoda_dfco2.py          # write oceansoda_dfco2.zarr next to this script
python oceansoda_dfco2.py --serve  # ... and serve an OpenLayers preview (blocks)
"""

import argparse
from pathlib import Path

import xarray as xr

import geozarr_pyramid_maker as gpm

URL = "https://s3.waw4-1.cloudferro.com/EarthCODE/OSCAssets/ocean-soda/dfco2.zarr/"
OUT = Path(__file__).parent / "oceansoda_dfco2.zarr"


def main(serve: bool = False) -> None:
    gpm.configure_logging("INFO")
    ds = xr.open_zarr(URL).isel(time=slice(0, 12))  # 12 steps, ~47 MiB

    print(ds.geozarr.plan())
    result = ds.geozarr.to_pyramid(OUT, overwrite=True)

    print("validation:", "ok" if gpm.is_valid(result.validation) else result.validation)
    if serve:
        gpm.preview(OUT, serve=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="serve the preview (blocks)")
    main(serve=parser.parse_args().serve)
