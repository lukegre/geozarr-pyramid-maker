# Research: polar_hackathon GeoZarr conversion (reference implementation)

Source: <https://esa-earthcode.github.io/polar_hackathon/geozarr-conversion/>
Notebook: `ESA-EarthCODE/polar_hackathon/4_Visualisation/2_geozarr_conversion.ipynb`
Case study: Antarctic basal melt, EPSG:3031, 6435×6120 px, float32. Summarised 2026-09-29.

## Pipeline
1. `xr.open_zarr(SRC, chunks={"y":512,"x":512})` → `rio.set_spatial_dims("x","y").rio.write_crs("EPSG:3031")` (the CRS is **hardcoded**).
2. Spatial vars = data_vars with an `x` or `y` dim → `.where(v != -9999).astype("float32")`, set `grid_mapping="spatial_ref"`.
3. Pyramid: factors `[1,2,4,8,16]`, built iteratively from the previous level with
   `coarsen(y=2, x=2, boundary="trim").mean(skipna=True)`.
4. Per level: `rio.transform()` gives `spatial:transform`, `rio.bounds()` gives `spatial:bbox`, and the sizes give `spatial:shape`;
   attrs are built with `geozarr_toolkit.create_spatial_attrs(...)`, `create_proj_attrs(code=CRS)` and
   `create_zarr_conventions(SpatialConventionMetadata(), ProjConventionMetadata())`.
5. Write prep: `fillna(-9999)`, `_FillValue`/`missing_value=-9999`, `spatial_ref` kept as a data variable, chunks
   `min(512, n)`, encodings cleared.
6. `ds.to_zarr(OUT, group=f"{NAME}/{i}", zarr_format=3, consolidated=False, encoding={v: {"compressors":[ZstdCodec(3)], "chunks":...}})`,
   then attrs are re-applied through `zarr.open_group(...).attrs.update(...)`.
7. The parent group `/{NAME}` gets `zarr_conventions` (multiscales + spatial + proj), `multiscales.layout`,
   `resampling_method="average"`, `spatial:bbox`, `spatial:dimensions` and `proj:code`.
8. Validation: `geozarr_toolkit.detect_conventions(attrs)` and `validate_group(group)`. Viewing: OpenLayers ≥10.8 `GeoZarr`
   source + `WebGLTileLayer`, served over HTTP with CORS and Range headers.

## Layout entries
```json
{"asset": "0", "spatial:shape": [6435, 6120], "spatial:transform": [1000,0,-3060277.25,0,-1000,3217259.75]}
{"asset": "1", "derived_from": "0", "transform": {"scale": [2.0,2.0], "translation": [0.0,0.0]}, "spatial:shape": [...], "spatial:transform": [...]}
```

## Weaknesses to fix in the package
| Issue in notebook | Package approach |
|---|---|
| CRS, dims, units and nodata hardcoded | Auto-detect (see PLAN §4.1) |
| `boundary="trim"` drops edge pixels, so bbox/transform drift and `translation=[0,0]` is only approximate | Pad to even size instead, so the origin stays fixed and translation `[0,0]` is exact |
| Everything forced to float32 with −9999 | Preserve dtype; floats use NaN, ints keep their fill value |
| `mean` used for every variable (wrong for masks/categorical data) | Resampling chosen per variable from dtype and CF flags |
| Fixed 5 levels | Levels derived automatically until the grid fits in one tile |
| Same 512 chunks at every level, no sharding | Auto chunk and shard per level |
| Each level's `to_zarr` recomputes the whole upstream dask chain | Write level *n*, then re-open it lazily to build *n+1* |
| Extra dims (time) only partly handled | Non-spatial dims handled explicitly |
