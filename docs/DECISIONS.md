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
Docs for v0.1 are the README plus `examples/`, with no docs site yet. The M6 reference test uses the OceanSODA-ETHZ-HR ΔfCO₂ store `https://s3.waw4-1.cloudferro.com/EarthCODE/OSCAssets/ocean-soda/dfco2.zarr/` (Zarr v3, 0.25° global, weekly 1982–, float32 NaN fill; marked `slow`/`network`) — changed from the notebook's basal-melt data by the user on 2026-09-29.

## D-16 — Override geozarr-toolkit's convention `schema_url`s · accepted 2026-09-29
toolkit 0.1.2 embeds `.../refs/tags/v1/schema.json` URLs that return 404, so we write the upstream `refs/tags/v0.1` URLs instead (and `zarr-conventions/proj` for proj:). The UUIDs are unchanged. **Why:** clients key on the UUID, but working URLs matter for humans and validators; revisit when the toolkit fixes them.

## D-17 — xarray floor `>=2026.2.0` · accepted 2026-09-29
sharded writes exist from 2025.01.2, but 2026.2.0 (PR #11117) fixed silent data corruption when dask chunks don't align with shards. **Why:** correctness; we align chunks to shards anyway, but this is defence in depth.

## D-18 — Metadata uses the spec's resampling vocabulary · accepted 2026-09-29
the user-facing option stays `resampling="mean"`, but `"average"` is written in the `resampling_method` metadata (the multiscales spec term). Other methods (nearest, mode, min, max) are written unchanged. **Why:** spec compliance while keeping the xarray-style API.

## D-19 — Consolidate metadata explicitly · accepted 2026-09-29
call `zarr.consolidate_metadata` at the end instead of `to_zarr(consolidated=True)`, and suppress only the `ZarrUserWarning` about consolidated metadata not being in the v3 spec. **Why:** refines D-07 without noisy warnings for users.

## D-20 — Integer fill values: 0 stays visible, NaNs explicitly hidden · accepted 2026-09-29
Zarr v3 needs a `fill_value` on every array, and viewers (e.g. OpenLayers GeoZarr) treat it as nodata. Floats: NaN is the fill and the only hidden value. Ints with a declared `_FillValue`/`missing_value`/nodata keep it. Ints with none get a sentinel on-disk `fill_value`: dtype min (signed) / max (unsigned), or the other end if the sentinel is in CF `flag_values`. No CF `_FillValue` attr is written for the sentinel, so xarray reads the ints unmasked. Padding and antimeridian gaps use the sentinel. Bool without a fill keeps `False` (limitation).
**Why:** the zarr default of 0 made valid zeros (e.g. an ocean class in a mask) transparent in viewers.

## D-21 — Preview config extras · accepted 2026-09-29
Per-variable `display_name` (LaTeX long_name cleaned), `units_display`, `ticks` (1/2/5×10ⁿ nice ticks, mirrored in JS `niceTicks` with shared test cases), per-dim `iso` UTC timestamps (`…Z`); existing keys unchanged.

## D-22 — Colourmaps · accepted 2026-09-29
`COLORMAPS` dict of 11-stop ramps (viridis, magma, inferno, cividis, turbo; RdBu_r, coolwarm, BrBG, PuOr) hard-coded from matplotlib (no runtime dependency); default viridis now 11 stops, `ramp` kept for back-compat; user adjustments are client-side only (localStorage `gpm-preview:style:<title>:<variable>`), Reversed resets on colourmap change; default vmin/vmax computation (2–98 % of the coarsest level, first extra-dim index) unchanged for now.

## D-23 — Basemaps are keyless only · accepted 2026-09-29
OSM tiles; Light and Dark are CSS filters on separate OSM layers (distinct className so the data layer is unfiltered); default follows prefers-color-scheme; only offered when basemap_default (EPSG:4326/3857).

## D-24 — No centre-longitude / wrapX for data · accepted 2026-09-29
Rejected, reverted in 8b33750: OL GeoZarr wraps by tile column, and derived tile widths (max 512 px from shard/chunk) don't divide the global width (level 0: 1440 px / 512 → 3 cols = 384°, 24° offset; level 1: 720/512 → 512°, 152°; only level 2 exact), giving a misaligned copy. Revisit only with write-side chunking where chunk widths divide global longitude widths at every level (would change D-06 planning); not planned.

## D-25 — Scrubber behaviour · accepted 2026-09-29
Arrows/step buttons clamp at ends, only play loops; selecting a card or pressing arrows stops playback; colliding tick labels hidden in JS.

## D-26 — Centre longitude via shifted layer copies · accepted 2026-09-29
For global geographic data (`global` in the preview config: geographic CRS, bbox 360° wide), the sidebar offers a centre longitude. Two extra copies (always, including a centre of 0°) of the data layer are added whose tile grids (extent and origins) are shifted by ±360°, so chunk indices are unchanged and the misalignment of D-24 cannot occur; the view extent is limited to lon ± 180°. Changing the value reloads the page with `?lon=` (also remembered in localStorage) because rebuilding the view in place left a blank map. Refines D-24 (wrapX stays rejected).

## D-27 — Store picker in the preview, checked server-side · accepted 2026-09-29
The sidebar title is an editable path box (local path, https://, s3:// or gs://). Enter calls the preview server's `GET /api/open?store=…`, which runs `check_store` in Python and returns either the page config or `{code, message, hint}`; the page reloads with `?store=…`. Local stores are served under `/_stores/<n>/` (path traversal returns 404); s3/gs URLs map to their public https endpoints. Error codes: `empty`, `not_found`, `not_zarr` (incl. NetCDF/GeoTIFF/GRIB files), `zarr_v2`, `not_group`, `not_pyramid` (hint: `geozarr-pyramid convert` / `ds.geozarr.to_pyramid`), `invalid_pyramid`, `missing_dependency`, `access_denied`, `unreachable`, `unsupported_scheme`; the page adds `no_server` and `browser_blocked` (Python can read it, the browser cannot: public access/CORS). `StoreError` subclasses ValueError (`StoreNotFoundError` also FileNotFoundError), so `preview()` and the CLI show the same messages. **Why:** one validation path, reuses the Python store stack; the browser alone cannot read local paths or diagnose formats.

## D-28 — Shareable URL state · accepted 2026-09-29
The page keeps its URL in sync (history.replaceState, debounced): `store`, `var`, one parameter per dim of the active variable (ISO date for time-like dims, else index), `cmap`, `rev`, `vmin`, `vmax`, `opacity` (if ≠ 1), `lon` (global data) and the map view `x`, `y`, `z`. On load, URL values override localStorage, so a shared link reproduces the view. Local store paths are shown with a `file://` prefix (in the path box and `store`), which the server strips; they stay as typed otherwise (not portable across machines, by design). Changing the store resets the other parameters; changing `lon` drops `x/y/z`.

## D-29 — Blank viewer · accepted 2026-09-29
Clearing the path box and pressing Enter opens `?store=` (empty), which shows the blank viewer even on a page generated for one store; the blank viewer keeps `store=` in its URL.
`render_html(None)` / `blank_config()` gives a page with no store: global EPSG:4326 view (bbox ±180/±90, `global: true`, centre-longitude control), basemap on, empty path box. `geozarr-pyramid preview` without STORE runs `serve_viewer(".")`, which serves it at `/` with `/api/open`; relative store paths resolve from the working directory. Loading a store reloads with `?store=…` and the page takes all variable defaults (names, units, dims, vmin/vmax percentiles, CRS, bbox) from that store via `check_store`/`read_config`; URL values still override. The default basemap is now always Dark (supersedes the prefers-color-scheme default in D-23). A View built in the page cannot be passed to Map (separate +esm bundles fail `instanceof`), so the blank view is passed as `Promise.resolve(options)`.

## D-30 — Viewer container for RenkuLab · accepted 2026-09-29
`Dockerfile` (python:3.12-slim + uv, `uv sync --frozen --no-dev --extra cloud`, user 1000:100, workdir `/home/renku/work`, port 8888) runs `docker/entrypoint.sh` → `geozarr-pyramid preview --host 0.0.0.0 --port 8888 --base-path $RENKU_BASE_URL_PATH` from `$RENKU_WORKING_DIR`. RenkuLab proxies under a path prefix without stripping it, so the server takes `base_path`: requests outside it 404, `<base>` redirects to `<base>/`, mounts are `<base>/_stores/<n>/`, and the served page gets `api_base` for `/api/open`. Relative store paths resolve from the served directory (not the process cwd); errors still show the path as typed. The `cloud` extra gains fsspec + aiohttp so https stores open in Python. Image built and pushed to Docker Hub (`lukegre/geozarr-viewer`) by `.github/workflows/docker.yml` (linux/amd64); `docker-compose.yml` for local runs.

## D-31 — Preview server relays remote stores the browser cannot fetch directly · accepted 2026-09-29
Buckets without CORS (e.g. `s3://spi-pamir-public/…` on `https://os.zhdk.cloud.switch.ch`) fail in the browser although Python reads them. The server keeps `server.remotes` (token → zarr Store, token = first 12 hex of sha1 of src + storage options) and serves `GET`/`HEAD` `<base>/remote/<token>/<key…>` with single `Range` support (`a-b`, `a-`, suffix `-n`; 206 + `Content-Range`), keys only: no listing, `..` segments and unknown tokens/keys 404, backend errors 502 (logged at INFO). `/api/open` and the CLI-baked page (`preview s3://… --serve`, new `--endpoint`) add `relay_url` next to the direct `store_url`; the page is **direct-first**: probe `store_url/zarr.json`, else `relay_url`, and only if both fail show `browser_blocked`. A relayed store is noted in the settings panel. **Read-only, and only for the localhost/RenkuLab session server** (same trust as `/api/open`, which already opens any path the server user can read). Side effect: private buckets become readable with the server's local credentials, so do not expose a preview server publicly.

## D-32 — Top bar, S3 endpoint in the sidebar, info panel · accepted 2026-10-01
The store path box lives in a permanent `#topbar` fixed to the top of the window at full width (`--top-h`, 44px), next to an info button (`#infobtn`); no more expand-on-focus. With the sidebar collapsed the top bar is hidden (`--top-h: 0`), so the spine and map reach the top; `#sbtoggle` stays in the sidebar. The S3 endpoint field (`#s3row`) moved from the settings panel to the top of the sidebar and is shown whenever the path box starts with `s3://`. The sun/settings button became an info button opening `#infopanel` (dropdown under the top bar, scrollable, closed by the button or Escape) with two tables built via textContent: Metadata (store, CRS, bbox, variables, dimensions, levels, relay note) and Global attributes. The config gains `attrs` (root attrs minus `multiscales`, `zarr_conventions`, `proj:*`, `spatial:*` and the variables-order attr; strings and finite numbers as-is, everything else stringified, strings truncated at 2000 chars) and `levels` (multiscales layout length); `blank_config()` has `{}` and `0`. Refines D-21 and D-27.
