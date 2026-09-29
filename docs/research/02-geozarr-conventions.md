# Research: GeoZarr conventions & tooling (state as of 2026-09)

> Compiled by a web-search agent; all ⚠ items were verified on 2026-09-29 (see "M0 verification" at the end).

## Spec status
- GeoZarr moved (Oct 2025) to **modular Zarr conventions**, each registered in the `zarr_conventions` attribute array
  with `uuid`, `name` and `schema_url`. GeoZarr v1.0 is heading to OGC review (~2026). GDAL ≥3.13 reads the conventions.
- Repos: `zarr-developers/geozarr-spec`, `zarr-conventions/multiscales`, `zarr-conventions/spatial`,
  `zarr-conventions/geo-proj` ✅ verified: canonical repo for `proj:` is now `zarr-conventions/proj` (tag `v0.1`; `zarr-experimental/geo-proj` is the older home). geozarr-toolkit 0.1.2 embeds `.../refs/tags/v1/schema.json` URLs which return HTTP 404 (upstream tag is `v0.1`); UUIDs are correct.

## `proj:` (CRS)
At least one of `proj:code` (e.g. `"EPSG:3031"`), `proj:wkt2` or `proj:projjson`. UUID `f17cb550-5864-4468-aeb7-f3180cfb622f` ✅ verified (toolkit + upstream README agree).

## `spatial:` (georeferencing)
```json
{"spatial:dimensions": ["y","x"], "spatial:transform": [a,b,c,d,e,f], "spatial:transform_type": "affine",
 "spatial:shape": [ny,nx], "spatial:bbox": [xmin,ymin,xmax,ymax], "spatial:registration": "pixel"}
```
`x = a*col + b*row + c`, `y = d*col + e*row + f` (rasterio/GDAL affine ordering).

## `multiscales`
```json
{"multiscales": {"layout": [
   {"asset": "0", "transform": {"scale": [1,1]}},
   {"asset": "1", "derived_from": "0", "transform": {"scale": [2,2], "translation": [0,0]}, "resampling_method": "average"}
 ], "resampling_method": "average"}}
```
`layout` is required, `asset` is the path of each level, `transform` is required when `derived_from` is present, and `resampling_method` can be set per level. ✅ Spec allows any string; its vocabulary uses `"average"` (not `"mean"`), `"nearest"`, `"bilinear"`, `"cubic"`, `"mode"`, `"max"`, `"min"`, `"med"`, `"sum"` etc. Use `"average"`.

## Tooling
| Package | Notes |
|---|---|
| zarr-python ≥3.1 | v3 format; `create_array(chunks=..., shards=...)`; `ZstdCodec` |
| xarray ≥2025.01.2 (recommend ≥2026.02.0) | `to_zarr(zarr_format=3)`; encoding accepts `chunks` + `shards` ✅ (added v2025.01.2, PR #9948; dask/shard alignment corruption fix in v2026.02.0, PR #11117) |
| geozarr-toolkit 0.1.x | Pydantic models, `create_*_attrs`, `create_zarr_conventions`, `validate_group`, CLI |
| rioxarray | CRS/transform detection, `spatial_ref` grid-mapping variable |
| odc-geo / xproj | Alternatives for CRS handling (not needed for v0.1) |

## Chunks, shards, compression
- Inner chunks of 256–512 px for visualisation (512 is standard for OpenLayers/deck.gl).
- Shards: aim for roughly 50–500 MB uncompressed; they must be **whole multiples of the chunk shape**.
- **Parallel dask writes must not have two tasks writing into one shard**: dask chunks must equal or evenly tile shards.
- zstd (level ~3) is the default compressor. Consolidated metadata helps remote clients.

## Clients
| Client | Reads |
|---|---|
| OpenLayers ≥10.8 (`ol/source/GeoZarr`) | multiscales + spatial + proj, native CRS (projection must be registered via proj4) |
| deck.gl-raster ZarrLayer | multiscales + spatial + proj; non-spatial dims must be selected |
| TiTiler(-EOPF / -multidim) | server-side, all conventions |
| GDAL ≥3.13 | conventions-aware Zarr driver |

Native-CRS pyramids are supported by all major clients, so WebMercatorQuad is not required.

## M0 verification (2026-09-29)

Throwaway venv (Python 3.14.0): xarray 2026.7.0, zarr 3.4.0, dask 2026.8.0, numpy 2.5.3, geozarr-toolkit 0.1.2, obstore 0.11.1, rioxarray 0.23.0, xproj 0.2.1, pyproj 3.8.0, pydantic 2.13.5.

### (a) xarray sharded writes
- `shards` is in `valid_encodings` in `xarray/backends/zarr.py` (`extract_zarr_variable_encoding`, together with `chunks`, `compressors`, `filters`, `serializer`, `write_empty_chunks`, `chunk_key_encoding`, `fill_value` for v3). Added in **xarray v2025.01.2** (PR #9948). Whats-new v2026.02.0 (PR #11117, issue #10831) fixed silent data corruption when dask chunks do not align with shards: `_validate_and_transform_encoding` now uses `encoding.get("shards") or encoding["chunks"]` as the effective write chunks (`safe_chunks` check / `align_chunks`). **Pin `xarray>=2026.2.0`** to get the fix.
- Tested: dask array (512x512 chunks) written with
  `encoding={"v": {"chunks": (256,256), "shards": (512,512), "compressors": [zarr.codecs.ZstdCodec(level=3)], "write_empty_chunks": False}}`,
  `to_zarr(store, group="0", mode="w", zarr_format=3, encoding=enc)`. Re-opened with `zarr.open_array`: `.chunks == (256,256)`, `.shards == (512,512)`, `.compressors == (ZstdCodec(level=3, checksum=False),)`. zarr.json codec is `sharding_indexed` (inner bytes+zstd, index crc32c). `write_empty_chunks` accepted in encoding, no error.
- `to_zarr(..., consolidated=True)` works with zarr_format=3 but emits `ZarrUserWarning: Consolidated metadata is currently not part in the Zarr format 3 specification...` (once per call). Wrap in `warnings.catch_warnings()` filtering that message, or consolidate explicitly (see notes).

### (b) geozarr-toolkit 0.1.2 public API
`__all__`: `Multiscales, MultiscalesConventionMetadata, Proj, ProjConventionMetadata, ScaleLevel, Spatial, SpatialConventionMetadata, Transform, ZarrConventionMetadata, __version__, create_geozarr_attrs, create_multiscales_layout, create_proj_attrs, create_spatial_attrs, create_zarr_conventions, detect_conventions, from_geotransform, from_rioxarray, validate_attrs, validate_group, validate_multiscales, validate_proj, validate_spatial`. (`MultiscalesAttrs`, `PROJ_UUID`, etc. live in `geozarr_toolkit.conventions`, not top-level.)

Signatures:
```python
create_spatial_attrs(dimensions: list[str], *, transform=None, bbox=None, shape=None, registration: str = "pixel") -> dict
create_proj_attrs(*, code: str | None = None, wkt2: str | None = None, projjson: dict | None = None) -> dict
create_zarr_conventions(*conventions: ZarrConventionMetadata) -> list[dict]
create_multiscales_layout(levels: list[dict], *, resampling_method: str | None = None) -> dict   # returns {"multiscales": {...}}
create_geozarr_attrs(dimensions, *, crs=None, transform=None, bbox=None, shape=None, registration="pixel", include_conventions=True) -> dict  # spatial+proj+zarr_conventions (no multiscales)
detect_conventions(attrs: dict) -> list[str]            # e.g. ['spatial','proj','multiscales']
validate_group(group: zarr.Group, conventions: list[str] | None = None) -> dict[str, list[str]]
validate_attrs(attrs: dict, conventions: list[str] | None = None) -> dict[str, list[str]]   # same, on a plain dict (no zarr needed)
from_rioxarray(da) -> dict ; from_geotransform(geotransform, crs_wkt, shape, dimensions=None) -> dict
```
- `validate_group` / `validate_attrs` return `{convention_name: [error strings]}`; keys among `spatial`, `proj`, `multiscales`, `zarr_conventions`; **empty list = valid** (never raises). Example on our group: `{'spatial': [], 'proj': [], 'multiscales': [], 'zarr_conventions': []}`. Bad input yields pydantic error strings (e.g. `"Value error, spatial:transform must have exactly 6 coefficients for 2D affine"`, `"... multiscales layout must have at least one level"`). It does not check that `asset` paths exist (separate `validate_multiscales_structure(group) -> (bool, list[str])` in `geozarr_toolkit.helpers.validation`, not exported).
- Quirks: `create_spatial_attrs` uses `if transform` / `if bbox` (falsy check) and always emits `spatial:transform_type: "affine"` and `spatial:registration`. `Proj` validator resolves `proj:code` through pyproj (`CRS.from_authority`) and requires pattern `^[A-Z]+:[0-9]+$`; use `wkt2=` for non-authority CRS. `create_multiscales_layout` requires `transform` as dict with `scale`/`translation` (tuples are converted to lists on dump; give floats). Convention entries dump with `uuid, schema_url, spec_url, name, description`.

Recommended snippet for the metadata module:
```python
from geozarr_toolkit import (
    MultiscalesConventionMetadata,
    ProjConventionMetadata,
    SpatialConventionMetadata,
    create_multiscales_layout,
    create_proj_attrs,
    create_spatial_attrs,
    create_zarr_conventions,
    validate_group,
)

attrs = {}
attrs.update(
    create_spatial_attrs(
        ["y", "x"], transform=[a, b, c, d, e, f], bbox=[xmin, ymin, xmax, ymax], shape=[ny, nx]
    )
)
attrs.update(create_proj_attrs(code="EPSG:3031"))  # or wkt2=crs.to_wkt()
attrs.update(
    create_multiscales_layout(
        [
            {"asset": "0"},
            {
                "asset": "1",
                "derived_from": "0",
                "transform": {"scale": [2.0, 2.0], "translation": [0.0, 0.0]},
            },
        ],
        resampling_method="average",
    )
)
attrs["zarr_conventions"] = create_zarr_conventions(
    MultiscalesConventionMetadata(), SpatialConventionMetadata(), ProjConventionMetadata()
)
root = zarr.open_group(store, mode="r+")
root.attrs.update(attrs)
errors = validate_group(
    zarr.open_group(store, mode="r")
)  # dict[str, list[str]]; all lists empty == valid
```
Produced attrs (abridged): `spatial:dimensions ["y","x"]`, `spatial:bbox`, `spatial:transform_type "affine"`, `spatial:transform [a,b,c,d,e,f]`, `spatial:shape`, `spatial:registration "pixel"`, `proj:code "EPSG:3031"`, `multiscales {"layout":[{"asset":"0"},{"asset":"1","derived_from":"0","transform":{"scale":[2.0,2.0],"translation":[0.0,0.0]}}],"resampling_method":"average"}`, `zarr_conventions [...]`. Note the toolkit does not require `transform` on level 0 (we omit it).

### (c) Convention identifiers (from toolkit constants, cross-checked with upstream READMEs)
| Convention | UUID | `name` | schema_url embedded by toolkit 0.1.2 | Status of that URL |
|---|---|---|---|---|
| multiscales | `d35379db-88df-4056-af3a-620245f8e347` | `multiscales` | `https://raw.githubusercontent.com/zarr-conventions/multiscales/refs/tags/v1/schema.json` | HTTP 404 (upstream tag is `v0.1`) |
| spatial: | `689b58e2-cf7b-45e0-9fff-9cfc0883d6b4` | `spatial:` | `https://raw.githubusercontent.com/zarr-conventions/spatial/refs/tags/v1/schema.json` | HTTP 404 (upstream `v0.1`) |
| proj: | `f17cb550-5864-4468-aeb7-f3180cfb622f` | `proj:` | `https://raw.githubusercontent.com/zarr-experimental/geo-proj/refs/tags/v1/schema.json` | HTTP 404; upstream now `https://raw.githubusercontent.com/zarr-conventions/proj/refs/tags/v0.1/schema.json` (200) |
UUIDs all match the upstream READMEs. Upstream `schema_url`s use `refs/tags/v0.1` (proj repo moved to `zarr-conventions/proj`). Clients key on the UUID, so the toolkit's dead URLs are harmless but untidy; to emit live URLs, pass overrides, e.g. `SpatialConventionMetadata(schema_url=".../spatial/refs/tags/v0.1/schema.json")`. Decision needed (propose D-16): keep toolkit defaults vs override with `v0.1` URLs.

### (d) ObjectStore / obstore
```python
import obstore.store, zarr
# obstore 0.11.1
obstore.store.from_url(url: str, *, config: S3Config|GCSConfig|AzureConfig|None=None, client_options: ClientConfig|None=None,
                       retry_config: RetryConfig|None=None, credential_provider=None, **kwargs) -> ObjectStore   # S3Store/GCSStore/AzureStore/LocalStore/MemoryStore
# zarr 3.4.0
zarr.storage.ObjectStore(store: T_Store, *, read_only: bool = False)   # zarr/storage/_obstore.py
store = zarr.storage.ObjectStore(obstore.store.from_url("s3://bucket/prefix", config={"region": "eu-central-1"}), read_only=False)
```
Tested: `from_url("memory:///")` -> `ObjectStore` -> `ds.to_zarr(store, group="0", zarr_format=3, mode="w", consolidated=False)` and `zarr.open_group(store)` works; `from_url("s3://...")` returns `S3Store`, `gs://` returns `GCSStore` (credentials resolved lazily from env). Prefix in the URL path is respected.

### (e) Accessor name `geozarr`
Not registered anywhere: `hasattr(xr.Dataset|DataArray|DataTree, "geozarr")` is False with geozarr-toolkit, rioxarray, xproj, obstore, zarr imported. Grep of site-packages: only `rio` (rioxarray) and `proj` (xproj) are registered (plus xarray's own tests). `geozarr` is free. Note `proj` is taken by xproj if installed, so do not name anything `.proj`. Register on Dataset and DataArray (and optionally DataTree).

### Extra notes
- `xr.open_datatree("t.zarr", engine="zarr")` on a root group with attrs and subgroups `0`, `1` works, no warnings: root attrs (spatial/proj/multiscales/zarr_conventions) appear as `dt.attrs`; `/0` and `/1` are Datasets. Root group created implicitly by `to_zarr(group="0")` has empty attrs; set them afterwards via `zarr.open_group(mode="r+").attrs.update(...)`.
- Sharded write behaviour: dask chunks must be a multiple of / align with `shards` (xarray >=2026.2 rechunks with `align_chunks` or raises via `safe_chunks`).
- The sandboxed `uv` in this environment needed `UV_CACHE_DIR` outside `~/.cache` and network access; irrelevant to the project.

### Contradictions / updates to PLAN
- `resampling_method`: use `"average"`, not `"mean"`.
- xarray floor should be `>=2026.2.0` (shard-alignment corruption fix), not just "2025.x".
- Toolkit-embedded `schema_url`s (`tags/v1`) 404 upstream; UUIDs OK.
- `consolidated=True` on zarr v3 emits a ZarrUserWarning.
