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
| `tile_size` | Inner chunk size, default `512`; an int, or `{var: size}` (unlisted variables keep the default) |
| `max_levels` | Limit the number of pyramid levels |
| `shard_size` | Shard shape in pixels: `None` (default, automatic, about 128 MiB), `n` (`n × n`), `{"time": 12, "y": 2048, "x": 2048}` (extra dims default to 1), or `{var: that}`; spatial values are rounded up to a multiple of the tile size; `-1` means the whole dim, and `"y"`/`"x"` are accepted for the spatial dims whatever they are called (D-59) |
| `keepbits` | Bit rounding of float variables (D-60): mantissa bits kept, an int (all floats) or `{var: bits}`; 0-23 for float32, 0-52 for float64; default `None` (off). Applied to every level, NaN and fill values pass through, recorded as the variable attribute `bitround_keepbits`. Better compression, lossy; CLI: `--keepbits 8` or `--keepbits sst=8` |
| `compression_level` | zstd level, default `3` |
| `overwrite` | Default `False` |
| `validate` | Validate the result, default `True` |
| `preview` | Also write a preview page, default `False` |
| `storage_options` | Passed to the store backend (cloud outputs) |

Use `gpm.configure_logging("INFO")` to see progress.

Full examples on real data:
[`examples/oceansoda_dfco2.py`](https://github.com/lukegre/geozarr-pyramid-maker/blob/main/examples/oceansoda_dfco2.py)
and [`examples/demo.ipynb`](https://github.com/lukegre/geozarr-pyramid-maker/blob/main/examples/demo.ipynb).

## Plot defaults

Set `cmap`, `vmin` and `vmax` on a variable's attrs to control how the viewer first shows it:

```python
da.attrs.update(cmap="cmo.thermal", vmin=270, vmax=305)
da.geozarr.to_pyramid("sst.zarr")
```

`cmap` accepts the viewer's colourmap names (`viridis`, `cmo.thermal`, `cmr.ember`, ...) and `viridis_r`-style names
for the reversed map. See [Viewer](viewer.md#plot-defaults-from-attrs) for the rules.

## CLI

```bash
geozarr-pyramid plan input.nc
geozarr-pyramid convert input.nc out.zarr --var melt --crs EPSG:3031 --tile-size 512 --overwrite
geozarr-pyramid convert dfco2.zarr out.zarr --resampling mean          # or --resampling melt=mean --resampling mask=mode
geozarr-pyramid convert in.nc out.zarr --tile-size 256 --tile-size mask=512 --shard-size melt=time:12,y:2048,x:2048  # per variable; shard shapes in pixels
geozarr-pyramid validate out.zarr
geozarr-pyramid preview out.zarr --serve --port 8000
geozarr-pyramid preview --demo                                          # blank-viewer server opened on a public MUR SST demo store (needs network)
```

Use `-v`/`-vv` for DEBUG/TRACE logs and `-q` for warnings only.

Next: [how it works](how-it-works.md) and the [viewer](viewer.md).
