# geozarr-pyramid-maker — Implementation plan

Status: **draft for review** · Author: Luke Gregor (with Claude) · Date: 2026-09-29

Formalises the approach in the ESA EarthCODE polar hackathon notebook
([summary](research/01-polar-hackathon-notebook.md)) as a small, robust Python package that turns any regular-grid
xarray Dataset into a **GeoZarr multiscale pyramid** (Zarr v3, sharded) using the current modular GeoZarr
conventions ([summary](research/02-geozarr-conventions.md)). Decisions are recorded in [DECISIONS.md](DECISIONS.md), and
progress in [PROGRESS.md](PROGRESS.md).

---

## 1. Goals / non-goals

**Goals (v0.1)**
- One call, with no required arguments beyond the output path: `ds.geozarr.to_pyramid("out.zarr")`.
- Handles regional (non-global) data, any projected or geographic CRS (including polar), and one or many variables sharing a grid.
- Extra non-spatial dims (time, depth, band) pass through.
- Everything lazy with dask and memory-bounded; parallel-safe writes.
- Zarr v3 output with chunks, shards and compression chosen automatically for each level.
- Metadata that passes `geozarr-toolkit` validation (multiscales + spatial + proj), plus CF `grid_mapping` for GDAL,
  QGIS and rioxarray.
- Accessor, CLI, dry-run `plan()`, automatic validation, optional HTML preview.
- Logging with loguru; packaged with uv.

**Non-goals (v0.1)**: reprojection or WebMercatorQuad tiling; curvilinear or irregular grids (e.g. native ocean-model
grids); appending or updating an existing pyramid; STAC generation.

---

## 2. User-facing API

```python
import xarray as xr
import geozarr_pyramid_maker as gpm  # importing registers the `.geozarr` accessor

ds = xr.open_dataset("basal_melt.nc", chunks={})

ds.geozarr.plan()  # dry run: prints levels, shapes, chunks, shards and sizes
ds.geozarr.to_pyramid("basal_melt.zarr")  # write + validate, returns a PyramidResult
ds[["melt", "mask"]].geozarr.to_pyramid("out.zarr")  # subset = multi-variable
ds["melt"].geozarr.to_pyramid("out.zarr")  # DataArray works too

tree = xr.open_datatree("basal_melt.zarr", engine="zarr")
gpm.validate("basal_melt.zarr")  # {convention: [errors]}
gpm.preview("basal_melt.zarr", serve=True)  # writes an OpenLayers page and starts a CORS server
```

Full signature (all keyword-only and optional):

```python
to_pyramid(
    store,                      # path | fsspec URL | zarr Store
    *,
    crs=None,                   # override auto-detection (anything pyproj.CRS accepts)
    resampling="auto",          # "auto" | method | {var: method}; methods: mean, nearest, mode, min, max
    tile_size=512,              # inner chunk edge in px (the viewer tile size)
    max_levels=None,            # default: until the grid fits in one tile
    shard_size="128MiB",        # target uncompressed shard size
    compression_level=3,        # zstd
    overwrite=False,            # never delete existing data silently
    validate=True,
    preview=False,
    storage_options=None,       # for fsspec; obstore is used for s3/gs/az when installed (D-13)
) -> PyramidResult             # path, plan, validation report, timings
```

CLI (typer), a thin wrapper over the same functions:
```
geozarr-pyramid convert IN OUT [--var NAME ...] [--crs EPSG:3031] [--tile-size 512] [--overwrite]
geozarr-pyramid plan IN
geozarr-pyramid validate STORE
geozarr-pyramid preview STORE [--serve] [--port 8000]
```
`IN` is anything `xr.open_dataset` can read (NetCDF, Zarr, and GeoTIFF through the rioxarray engine).

---

## 3. Output layout

Levels sit at the root and hold all variables ([D-02](DECISIONS.md#d-02)):

```
out.zarr/                     zarr.json: zarr_conventions[multiscales, spatial:, proj:], multiscales,
│                                        spatial:dimensions, spatial:bbox, proj:code|wkt2, source global attrs, history
├── 0/                        native resolution. zarr.json: spatial:transform|shape|bbox|registration, proj:*
│   ├── x, y                  1-D coords (recomputed from the transform)
│   ├── spatial_ref           CF grid-mapping var (crs_wkt, …) for GDAL/rioxarray/QGIS
│   ├── melt                  (time?, y, x) attrs: units, long_name, grid_mapping, resampling_method
│   └── mask
├── 1/                        2× coarser
└── N/                        fits in one tile
```

Multiscales layout entry for each level `k > 0`:
`{"asset": "k", "derived_from": "k-1", "transform": {"scale": [2, 2], "translation": [0, 0]},
"spatial:shape": [...], "spatial:transform": [...], "resampling_method": ...}`.
Because odd edges are padded rather than trimmed (§4.3), the origin never moves, so `translation: [0,0]` is **exact**.

---

## 4. Processing design

### 4.1 Detection (`detect.py`), which is what makes "no user input" possible
| What | Detection order | On failure |
|---|---|---|
| Spatial dims | rioxarray's `x/y`, `lon/lat`, `longitude/latitude`; CF `axis="X"/"Y"`; `standard_name` (`projection_x_coordinate`, `longitude`, …) | Raise, listing the dims found and suggesting `ds.rename()` |
| Grid regularity | 1-D coords with constant spacing (relative tolerance 1e-6) | Raise "curvilinear or irregular grids are not supported in v0.1" |
| CRS | `crs=` argument → `ds.rio.crs` (spatial_ref / grid_mapping var) → `pyproj.CRS.from_cf(grid_mapping attrs)` → lon/lat in degree ranges gives EPSG:4326 (with a warning) | Raise and suggest `crs=` |
| Orientation | y ascending → flipped to north-up (descending), logged | — |
| Longitude 0–360 | EPSG:4326, global span: roll to −180…180 (logged) | Regional data crossing 180° is also rolled (split grid), with a WARNING ([D-11](DECISIONS.md#d-11)) |
| Variables | data_vars with **both** spatial dims; the others are dropped with a warning listing them | Vars on a different grid: raise |
| Fill value | `_FillValue`, `missing_value`, `rio.nodata` → floats: masked to NaN and written with NaN fill; ints: fill kept | — |

### 4.2 Plan (`plan.py`), a pure function that is fully unit-testable without writing
`PyramidPlan` is a frozen dataclass: CRS, dims, per-variable resampling, and a list of `LevelPlan`s
(`shape`, `transform`, `bbox`, `chunks`, `shards`, `nbytes`). Its `__repr__` prints a table; `ds.geozarr.plan()` returns it.

- **Number of levels**: keep halving until `max(ny, nx) <= tile_size` (at least 1 level). `max_levels` caps it.
- **Level shape**: `ceil(n / 2)` at each step (padding).
- **Level transform**: `a, e` ×2 per level; `c, f` (origin) unchanged.

### 4.3 Resampling (`resample.py`)
- Level `k+1` is built from level `k` **re-opened lazily from the store** after it is written. This keeps each dask graph
  shallow and memory bounded, and avoids the notebook's recomputation of the whole upstream chain ([D-05](DECISIONS.md#d-05)).
- `auto` rule for each variable: float → `mean` (skipna); int, bool, or anything with CF `flag_values`/`flag_meanings` →
  `mode` ([D-14](DECISIONS.md#d-14)). Can be overridden globally or per variable.
- `mean|min|max`: `coarsen(y=2, x=2, boundary="pad")`. `nearest`: strided `isel(::2)`, which gives the same `ceil(n/2)` shape
  with no dtype change. `mode`: vectorised 2×2 block mode via `map_blocks` (ties go to top-left, fill values ignored).
- dtype is preserved (ints stay ints; floats stay in their precision). Attributes are carried through.
- The method is recorded on each variable (`resampling_method`) and in `multiscales`: the top-level `resampling_method` when
  every variable uses the same method, otherwise per variable only.

### 4.4 Chunks and shards (`chunking.py`) ([D-06](DECISIONS.md#d-06))
For each level and variable, with `itemsize` from the dtype:
1. **Inner chunk**: spatial `tile_size × tile_size` (clipped to the shape); every non-spatial dim is 1 (one time step per tile,
   which is what viewers request).
2. **Shard (spatial)**: `k × k` chunks, where `k` is the largest power of 2 with `k²·tile²·itemsize <= shard_size`
   (float32 at 512 px gives k=8, so 4096² px and 64 MiB shards).
3. **Shard (non-spatial)**: if the spatial shard covers the whole level and is still below the target, extend the shard along
   the leading non-spatial dim (e.g. several time steps per shard) to reduce the object count at coarse levels.
4. **No shard** when a level has 4 chunks or fewer (sharding would add nothing).
5. **Dask chunks = shard shape** before writing. Each task writes whole shards, so parallel writes are safe with no locks, and
   each task holds about 64–128 MiB.
6. Codec: `ZstdCodec(level=3)`. Coords are written unsharded, as a single chunk.

### 4.5 Metadata (`metadata.py`)
Built with `geozarr-toolkit` models and helpers (`create_spatial_attrs`, `create_proj_attrs`,
`create_zarr_conventions`, multiscales model) rather than handwritten dicts, so it follows the spec as the toolkit evolves.
- `proj:code` whenever the CRS has an authority code, and **always** `proj:wkt2` for robustness.
- `spatial:registration = "pixel"`, `spatial:transform_type = "affine"`.
- CF: `grid_mapping="spatial_ref"` on every data var, and a `spatial_ref` variable written via `rio.write_crs`.
- Root attrs: the source's global attrs, with `history` appended ("created by geozarr-pyramid-maker vX.Y on …").
- Zarr **consolidated metadata** written at the end (one request for remote clients) ([D-07](DECISIONS.md#d-07)).

### 4.6 Writing (`write.py`)
1. Build the plan, log a summary, and fail early (before any compute) on detection errors or if the store exists and `overwrite=False`.
2. If the input is not dask-backed, chunk it (logged).
3. For each level: rechunk to shard shape, `to_zarr(group=str(k), zarr_format=3, mode="w", encoding={...chunks, shards, compressors, fill_value})`,
   then write the level attrs. Uses whichever dask scheduler is active (a distributed Client if present).
4. Write the root attrs, consolidate, and validate (`validate=True`). A validation failure is logged as ERROR and stored in
   `PyramidResult` (it does not raise) so the written data is not lost.
5. Preview, if requested.

### 4.7 Validation and preview
- `validate.py`: wraps `geozarr_toolkit.validate_group` for the root and every level, and also checks that
  `xr.open_datatree` round-trips and that the shapes match the plan.
- `preview.py`: an HTML template (OpenLayers ≥10.9 `GeoZarr` source + `WebGLTileLayer`, proj4 registration from the
  store's WKT, a variable dropdown, a colour range from the 2–98 % percentiles of the coarsest level, and the first
  index of any non-spatial dim). `serve=True` starts a CORS/Range-capable `http.server` (as in the notebook).

### 4.8 Logging (`_logging.py`) ([D-08](DECISIONS.md#d-08))
loguru with no handlers configured by the library:
- `INFO`: milestones (detected CRS/dims/vars, number of levels, per-level write time and size, validation result).
- `DEBUG`: the plan table and per-variable resampling choices.
- `TRACE`: chunk/shard arithmetic (hidden by loguru's default DEBUG handler).
- `WARNING`: auto-corrections (y flip, lon roll, dropped variables, inferred CRS).
- `gpm.configure_logging(level="INFO")` helper; the CLI calls it (`-v`/`-q`). `LOGURU_LEVEL` is respected.

---

## 5. Repository structure

```
geozarr-pyramid-maker/
├── pyproject.toml            uv_build; deps; [dependency-groups] dev; ruff/pytest config
├── uv.lock
├── README.md                 quickstart
├── docs/
│   ├── PLAN.md               this file
│   ├── DECISIONS.md          decision log (ADR-lite)
│   ├── PROGRESS.md           progress log (maintained by the tracker agent)
│   └── research/             source summaries
├── src/geozarr_pyramid_maker/
│   ├── __init__.py           public API: validate, preview, configure_logging, PyramidPlan; registers accessor
│   ├── accessor.py           GeoZarrDatasetAccessor / GeoZarrDataArrayAccessor ("geozarr")
│   ├── detect.py             dims, CRS, regularity, orientation, fill values
│   ├── plan.py               PyramidPlan, LevelPlan
│   ├── chunking.py           chunk/shard heuristics
│   ├── resample.py           mean/nearest/mode/min/max
│   ├── metadata.py           GeoZarr + CF attrs (via geozarr-toolkit)
│   ├── write.py              orchestration
│   ├── validate.py
│   ├── preview.py + templates/preview.html
│   ├── cli.py                typer app → [project.scripts] geozarr-pyramid
│   └── _logging.py
├── tests/
│   ├── conftest.py           synthetic dataset factories (see §6)
│   ├── test_detect.py  test_plan.py  test_chunking.py  test_resample.py
│   ├── test_metadata.py  test_write.py (round-trip + validation)  test_cli.py
│   └── test_reference.py     reproduces the notebook's basal-melt output (marked slow/network)
└── examples/
    └── polar_basal_melt.py
```

**Dependencies** (initial lower bounds, to be confirmed in M0): `xarray>=2025.6`, `zarr>=3.1`, `dask[array]>=2025.1`,
`numpy>=2`, `pyproj>=3.7`, `rioxarray>=0.19`, `geozarr-toolkit>=0.1.2`, `loguru>=0.7`, `typer>=0.12`.
Optional extra `[cloud]`: `obstore` (D-13; the fsspec fallback needs `fsspec` + `s3fs`/`gcsfs` from the user).
Dev: `pytest`, `pytest-cov`, `ruff`, `netcdf4` (fixtures), `pre-commit`. `requires-python = ">=3.12"` ([D-04](DECISIONS.md#d-04)).

---

## 6. Test matrix (synthetic, small, fast)

| Fixture | Exercises |
|---|---|
| Global EPSG:4326, 0–360 lon, y ascending | lon roll, y flip, CRS inference |
| Regional UTM (EPSG:32632), odd shape 1001×777 | padding, exact transforms, regional bbox |
| Polar EPSG:3031 with `spatial_ref` | CRS from grid mapping, the notebook's case |
| Multi-var: float `melt(time,y,x)` + int8 `mask(y,x)` + scalar var | per-var resampling, dropped non-spatial var, time sharding |
| Tiny (100×100) | single level, no sharding |
| No CRS info, projected coords | clear error message |
| Curvilinear 2-D lat/lon | clear error message |
| NumPy-backed input | auto-chunking |

Assertions: `plan()` shapes, transforms and shards; level k+1 values equal a NumPy reference coarsen; `geozarr_toolkit`
validation passes; `xr.open_datatree` round-trips; `rioxarray` reads the CRS at every level; each dask task writes one shard.

---

## 7. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | Scaffold: set `requires-python>=3.12`; add deps with uv; ruff, pytest, pre-commit; GitHub Actions CI (py3.12–3.14); MIT `LICENSE`; pyproject description; `[project.scripts] geozarr-pyramid`. **Verify** (Sonnet reading task, noted in research/02): (a) minimum xarray version for `encoding={'shards': ...}` with `zarr_format=3`, (b) the geozarr-toolkit multiscales/spatial/proj helpers and `validate_group` signatures, (c) the canonical `proj:` convention schema URL and UUID, (d) the `zarr.storage.ObjectStore` + obstore API, (e) that the accessor name `geozarr` is free | `uv run pytest` green on a smoke test; `uv run ruff check` clean; verification results in research/02 with the ⚠ markers resolved |
| M1 | `detect.py` + `plan.py` + `chunking.py` + `ds.geozarr.plan()` | Detection and plan tests pass for every fixture |
| M2 | `resample.py` + `write.py` (data only) | Round-trip tests; values match the reference |
| M3 | `metadata.py` + `validate.py` | geozarr-toolkit validation passes for every fixture |
| M4 | Logging polish + CLI | CLI tests; log output reviewed |
| M5 | Preview HTML + server | Manual check of the notebook dataset in a browser |
| M6 | README, example, reference test against the notebook data | Output matches the notebook (within padding differences) |

TDD throughout: tests are written first for each module.

### Agent delegation

Opus (the main session) plans, breaks work into tasks, reviews the diffs and makes design calls. It does not write code itself.

| Task | Model : effort | Notes |
|---|---|---|
| Planning, task breakdown, review, design decisions | Opus (main session) | |
| **Coding** (almost all implementation, tests, refactors) | **Sonnet : medium** (subagent) | The default for every coding task |
| Coding when tests **keep failing** after a Sonnet:medium attempt | Sonnet : high | Escalate only after a real failed attempt; pass along the failing output |
| Extreme cases only (Sonnet:high is stuck, or a subtle design/algorithm problem) | Opus : low → medium | Last resort; say in PROGRESS.md why it was needed |
| Reading tasks (code/docs digests, API lookups in installed packages) | Sonnet : medium | |
| Web searches | Haiku : high | Results are verified before being relied on (see ⚠ items in research/) |
| Progress tracking (`docs/PROGRESS.md`) | Haiku : high | Updated after each step or milestone |

Escalation ladder: Sonnet:medium → Sonnet:high → Opus:low → Opus:medium. Each coding task given to a subagent includes the
relevant PLAN/DECISIONS sections, the files to touch, the tests to make pass (TDD: red → green), and the rule "run `uv run pytest`
and `uv run ruff check` before reporting".

---

## 8. Resolved questions (2026-09-29)

- **Q1** Antimeridian: always roll to −180…180, with a warning → [D-11](DECISIONS.md#d-11)
- **Q2** Curvilinear grids: out of scope for v0.1 → [D-12](DECISIONS.md#d-12)
- **Q3** Cloud: obstore by default with an fsspec fallback; optional `[cloud]` extra → [D-13](DECISIONS.md#d-13)
- **Q4** Categorical default: `mode` → [D-14](DECISIONS.md#d-14)
