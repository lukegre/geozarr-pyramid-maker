# geozarr-pyramid-maker

Turn any regular-grid xarray Dataset (NetCDF, Zarr, GeoTIFF) into a **GeoZarr multiscale pyramid**: Zarr v3, sharded,
in the native CRS, with the modular GeoZarr conventions and CF `grid_mapping` metadata. One call, no required options:
`ds.geozarr.to_pyramid("out.zarr")`. Everything is lazy with dask, so data larger than memory is fine. The result is
ready for web viewers (OpenLayers, TiTiler, ...) and still opens with xarray, GDAL and QGIS.

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

`to_pyramid` options (all keyword-only): `crs`, `resampling` (`"auto"`, a method, or `{var: method}`; methods `mean`,
`nearest`, `mode`, `min`, `max`), `tile_size=512`, `max_levels`, `shard_size="128MiB"`, `compression_level=3`,
`overwrite=False`, `validate=True`, `preview=False`, `storage_options`. Use `gpm.configure_logging("INFO")` to see progress.

A full example on real data is in [`examples/oceansoda_dfco2.py`](examples/oceansoda_dfco2.py) and
[`examples/demo.ipynb`](examples/demo.ipynb).

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

## Output layout

```
out.zarr/                 zarr.json: zarr_conventions (multiscales, spatial:, proj:), multiscales,
│                                    spatial:dimensions/bbox, proj:code|wkt2, source attrs, history
├── 0/                    native resolution
│   ├── x, y              1-D coords (recomputed from the transform)
│   ├── spatial_ref       CF grid-mapping variable (crs_wkt, ...)
│   └── <variables>       (extra dims..., y, x); attrs incl. grid_mapping, resampling_method
├── 1/                    2x coarser
└── N/                    fits in one tile
```

## How it works

- **Detection**: spatial dims (`x/y`, `lon/lat`, CF `axis`, `standard_name`), constant grid spacing, CRS (`crs=`,
  then `spatial_ref`/`grid_mapping`, else lon/lat ranges imply EPSG:4326 with a WARNING), y flipped to north-up,
  0-360 longitudes rolled to -180..180. Variables without both spatial dims are dropped with a warning.
- **Padding, not trimming (D-06)**: each level has `ceil(n/2)` pixels, so odd edges are padded, no data is lost, the
  origin never moves and `translation: [0, 0]` is exact. Levels are added until the grid fits in one tile.
- **Resampling (D-09, D-14, D-18)**: floats use `mean` (NaN-aware); ints, bools and CF flag variables use `mode`
  (2x2 block mode, ties to the top-left, fill ignored). dtype is preserved. The method is stored on each variable and in
  `multiscales` (`mean` is written as `average`, the spec term). Level k+1 is built from level k re-read from the store.
- **Chunks and shards**: inner chunks are `tile_size` squares with 1 along every non-spatial dim. Shards are
  power-of-2 multiples of tiles up to `shard_size` (extended along the leading extra dim, e.g. time, at coarse levels),
  none for levels with 4 chunks or fewer. Dask chunks equal shards, so parallel writes need no locks. Codec: zstd.
- **GeoZarr conventions**: multiscales, `spatial:` and `proj:` attributes are built with `geozarr-toolkit` helpers,
  not handwritten dicts (`proj:code` when available, always `proj:wkt2`). Consolidated metadata is written at the end.
- **CF grid_mapping**: every variable carries `grid_mapping = "spatial_ref"` and a `spatial_ref` variable is written,
  so GDAL, QGIS and rioxarray see the CRS.
- **Integer fill rule (D-20)**: Zarr v3 needs a `fill_value` and viewers treat it as nodata. Floats use NaN. Ints keep a
  declared `_FillValue`; ints without one get a sentinel (dtype min for signed, max for unsigned) so valid zeros stay
  visible. Padding and antimeridian gaps use the sentinel.

## Known limitations

- No reprojection or WebMercator tiling: levels stay in the source CRS.
- Curvilinear and irregular grids are not supported (detection raises).
- GDAL older than 3.13 cannot read the GeoZarr conventions (it still reads the CF `spatial_ref` metadata per level).
- OpenLayers uses a per-level resolution derived from the extent, which can differ slightly from the padded level
  resolution; see [docs/research/03-openlayers-geozarr.md](docs/research/03-openlayers-geozarr.md).
- Bool variables without a declared fill keep `False` as fill (no sentinel is possible).

## Viewer image (RenkuLab and Docker)

The `Dockerfile` builds an image that starts the blank viewer (`geozarr-pyramid preview` with no
store) on port 8888. Type a store path or URL into the sidebar to open any pyramid; relative paths
resolve from the working directory. CI (`.github/workflows/docker.yml`) publishes it to Docker Hub as
`lukegre/geozarr-viewer` on every push to `main` (needs repo secrets `DOCKERHUB_USERNAME` and
`DOCKERHUB_TOKEN`).

**RenkuLab session launcher**: add a custom environment with

| Field | Value |
|---|---|
| Container image | `lukegre/geozarr-viewer:latest` |
| Default URL | `/` |
| Port | `8888` |
| UID / GID | `1000` / `100` |
| Mount directory / Working directory | `/home/renku/work` |
| Command / Arguments | leave empty (the image's entrypoint is used) |

The server reads `RENKU_BASE_URL_PATH` (RenkuLab does not strip it) and `RENKU_WORKING_DIR`, so
data connectors and project files under the working directory open by relative path. Keep the Docker Hub
repository public, or add registry credentials in RenkuLab.

**Locally**: `docker compose up --build`, then open <http://127.0.0.1:8888/>. Stores in `./data`
(or `DATA_DIR=/path/to/stores`) are mounted as the working directory. Set `RENKU_BASE_URL_PATH`
to try a URL prefix.

## Development

```bash
uv sync
uv run pytest                        # offline and fast (slow/network tests excluded)
uv run pytest -m "slow or network"   # reference test on OceanSODA data + demo notebook (needs network)
uv run ruff check
```

Design: [docs/PLAN.md](docs/PLAN.md), decisions: [docs/DECISIONS.md](docs/DECISIONS.md).

## License

MIT, see [LICENSE](LICENSE).
