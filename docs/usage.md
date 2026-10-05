# Usage

!!! warning "Experimental"
    This is an experimental approach. APIs and output layout can change without notice.

## Install

```bash
uv add geozarr-pyramid-maker            # core
uv add "geozarr-pyramid-maker[cloud]"   # + obstore, for s3:// gs:// az:// outputs
```

## Quickstart

```python
import xarray as xr
import geozarr_pyramid_maker as gpm  # importing registers the `.geozarr` accessor

ds = xr.open_dataset("basal_melt.nc", chunks={})

ds.geozarr.plan()  # dry run: levels, shapes, chunks, shards, sizes
result = ds.geozarr.to_pyramid("basal_melt.zarr")  # write + validate -> PyramidResult
ds[["melt", "mask"]].geozarr.to_pyramid("out.zarr")  # subset = multi-variable
ds["melt"].geozarr.to_pyramid("out.zarr")  # DataArray works too

gpm.validate("basal_melt.zarr")  # {convention: [errors]}
gpm.is_valid(result.validation)  # True / False
gpm.preview("basal_melt.zarr", serve=True)  # OpenLayers page + local CORS/Range server

tree = xr.open_datatree("basal_melt.zarr", engine="zarr")  # levels "0" ... "N"
```

## `to_pyramid` options

All keyword-only:

| Option | Meaning |
|---|---|
| `crs` | Override or supply the CRS of the source |
| `resampling` | `"auto"`, a method, or `{var: method}`; methods `mean`, `nearest`, `mode`, `min`, `max` |
| `tile_size` | Inner chunk size, default `512` |
| `max_levels` | Limit the number of pyramid levels |
| `shard_size` | Maximum shard size, default `"128MiB"` |
| `compression_level` | zstd level, default `3` |
| `overwrite` | Default `False` |
| `validate` | Validate the result, default `True` |
| `preview` | Also write a preview page, default `False` |
| `storage_options` | Passed to the store backend (cloud outputs) |

Use `gpm.configure_logging("INFO")` to see progress.

Full examples on real data:
[`examples/oceansoda_dfco2.py`](https://github.com/lukegre/geozarr-pyramid-maker/blob/main/examples/oceansoda_dfco2.py)
and [`examples/demo.ipynb`](https://github.com/lukegre/geozarr-pyramid-maker/blob/main/examples/demo.ipynb).

## CLI

```bash
geozarr-pyramid plan input.nc
geozarr-pyramid convert input.nc out.zarr --var melt --crs EPSG:3031 --tile-size 512 --overwrite
geozarr-pyramid convert dfco2.zarr out.zarr --resampling mean          # or --resampling melt=mean --resampling mask=mode
geozarr-pyramid validate out.zarr
geozarr-pyramid preview out.zarr --serve --port 8000
geozarr-pyramid preview --demo                                          # blank-viewer server opened on a public MUR SST demo store (needs network)
```

Use `-v`/`-vv` for DEBUG/TRACE logs and `-q` for warnings only.

Next: [how it works](how-it-works.md) and the [viewer](viewer.md).
