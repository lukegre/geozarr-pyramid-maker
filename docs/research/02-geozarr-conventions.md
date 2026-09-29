# Research: GeoZarr conventions & tooling (state as of 2026-09)

> Compiled by a web-search agent. Items marked ⚠ need verifying against the source when implementing.

## Spec status
- GeoZarr moved (Oct 2025) to **modular Zarr conventions**, each registered in the `zarr_conventions` attribute array
  with `uuid`, `name` and `schema_url`. GeoZarr v1.0 is heading to OGC review (~2026). GDAL ≥3.13 reads the conventions.
- Repos: `zarr-developers/geozarr-spec`, `zarr-conventions/multiscales`, `zarr-conventions/spatial`,
  `zarr-conventions/geo-proj` (the notebook used `zarr-experimental/geo-proj` tag v1 ⚠ check the canonical URL).

## `proj:` (CRS)
At least one of `proj:code` (e.g. `"EPSG:3031"`), `proj:wkt2` or `proj:projjson`. UUID `f17cb550-5864-4468-aeb7-f3180cfb622f` ⚠.

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
   {"asset": "1", "derived_from": "0", "transform": {"scale": [2,2], "translation": [0,0]}, "resampling_method": "mean"}
 ], "resampling_method": "mean"}}
```
`layout` is required, `asset` is the path of each level, `transform` is required when `derived_from` is present, and `resampling_method` can be set per level.

## Tooling
| Package | Notes |
|---|---|
| zarr-python ≥3.1 | v3 format; `create_array(chunks=..., shards=...)`; `ZstdCodec` |
| xarray ≥2025.x | `to_zarr(zarr_format=3)`; encoding accepts `chunks` + `shards` ⚠ confirm the minimum version |
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
