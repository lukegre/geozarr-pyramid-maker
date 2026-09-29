# Decision log

Short ADR-style entries. Status: **accepted** (chosen by Luke), **proposed** (Claude's default, open to change).

## D-01 — Native CRS, no reprojection · accepted 2026-09-29
Pyramid levels stay in the source CRS and are coarsened 2× per level. There is no WebMercatorQuad.
**Why:** values are not resampled across projections; polar and regional data work as is; OpenLayers, deck.gl, TiTiler
and GDAL all read native-CRS GeoZarr.

## D-02 — Levels at root, all variables per level · accepted 2026-09-29
`/0 … /N`, each containing every variable, with the multiscales metadata on the root group (EOPF/Sentinel-2 style).
**Why:** one pyramid, one `open_datatree`, and a viewer can switch variables within one source.

## D-03 — v0.1 scope includes validation, CLI, dry-run plan, preview HTML · accepted 2026-09-29

## D-04 — `requires-python >= 3.12` · accepted 2026-09-29
Development happens locally on 3.14; CI tests 3.12–3.14.

## D-05 — Build level k+1 by re-reading level k from the store · accepted 2026-09-29
**Why:** a shallow dask graph for each level and bounded memory. It avoids recomputing the full upstream chain per level (as the notebook does).
**Cost:** one extra read of each level (cheap next to the write).

## D-06 — Pad, don't trim, odd edges; auto chunks and shards · accepted 2026-09-29
Padding gives `ceil(n/2)` shapes, a fixed origin and an exact `translation: [0,0]`, and no data is dropped. Inner chunks are
`tile_size` (512) px; shards are power-of-2 multiples up to about 128 MiB; dask chunks equal shards, so writes are safe in parallel.

## D-07 — Consolidated metadata · accepted 2026-09-29
Zarr v3 consolidated metadata is not yet part of the core spec, but xarray and zarr-python support it and it saves remote
round trips. It can be switched off if a target client has problems with it.

## D-08 — loguru without configuring handlers in the library · accepted 2026-09-29
Levels: INFO for milestones, DEBUG for the plan, TRACE for internals, WARNING for auto-corrections. `configure_logging()` helper; the CLI configures it.
**Why:** the library must not hijack the application's logging; TRACE keeps loguru's default sink readable.

## D-09 — Resampling chosen per variable by dtype · accepted 2026-09-29
float → mean (skipna); int, bool or CF flags → **mode** (see D-14); nearest, min and max are opt-in. dtype is preserved.

## D-10 — Accessor name `geozarr` · accepted 2026-09-29
Check for clashes with geozarr-toolkit and xproj in M0; the fallback is `gzpyr`.

## D-11 — 0–360 longitudes are always rolled to −180…180 · accepted 2026-09-29
This includes regional EPSG:4326 data crossing the antimeridian, where it produces a split grid with an empty middle. A WARNING
explains this. Empty chunks are not written (`write_empty_chunks=False`), so the gap costs almost no storage.
**Why:** every viewer expects −180…180.

## D-12 — Curvilinear / ocean-model grids out of scope for v0.1 · accepted 2026-09-29
Detection raises a clear error. Revisit in v0.2.

## D-13 — Cloud stores through obstore (with fsspec fallback) · accepted 2026-09-29
`s3://`, `gs://` and `az://` URLs are turned into `zarr.storage.ObjectStore` (obstore, Rust-backed and fast, the modern zarr-python
path) when the `[cloud]` extra is installed; otherwise they go through `FsspecStore`. A ready-made zarr `Store` object is also accepted.

## D-14 — `mode` is the auto default for categorical/int variables · accepted 2026-09-29
Implemented as a vectorised 2×2 block mode: reshape to `(..., ny/2, 2, nx/2, 2)` and count by pairwise equality. Ties
go to the top-left pixel and fill values are ignored. This is cheap enough that nearest isn't needed as the default.

## D-15 — Project housekeeping · accepted 2026-09-29
MIT license. Personal GitHub with Actions CI (py3.12–3.14); PyPI via trusted publishing after v0.1 is stable.
Docs for v0.1 are the README plus `examples/`, with no docs site yet. The M6 reference test uses the notebook's basal-melt data (marked `slow`/`network`).

## D-16 — Override geozarr-toolkit's convention `schema_url`s · accepted 2026-09-29
toolkit 0.1.2 embeds `.../refs/tags/v1/schema.json` URLs that return 404, so we write the upstream `refs/tags/v0.1` URLs instead (and `zarr-conventions/proj` for proj:). The UUIDs are unchanged. **Why:** clients key on the UUID, but working URLs matter for humans and validators; revisit when the toolkit fixes them.

## D-17 — xarray floor `>=2026.2.0` · accepted 2026-09-29
sharded writes exist from 2025.01.2, but 2026.2.0 (PR #11117) fixed silent data corruption when dask chunks don't align with shards. **Why:** correctness; we align chunks to shards anyway, but this is defence in depth.

## D-18 — Metadata uses the spec's resampling vocabulary · accepted 2026-09-29
the user-facing option stays `resampling="mean"`, but `"average"` is written in the `resampling_method` metadata (the multiscales spec term). Other methods (nearest, mode, min, max) are written unchanged. **Why:** spec compliance while keeping the xarray-style API.

## D-19 — Consolidate metadata explicitly · accepted 2026-09-29
call `zarr.consolidate_metadata` at the end instead of `to_zarr(consolidated=True)`, and suppress only the `ZarrUserWarning` about consolidated metadata not being in the v3 spec. **Why:** refines D-07 without noisy warnings for users.
