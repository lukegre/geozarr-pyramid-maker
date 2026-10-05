# How it works

!!! warning "Experimental"
    This is an experimental approach. The output layout and GeoZarr conventions used here may change.

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

## Processing

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

See the [decision log](DECISIONS.md) and the [plan](PLAN.md) for the reasoning.

## Known limitations

- No reprojection or WebMercator tiling: levels stay in the source CRS.
- Curvilinear and irregular grids are not supported (detection raises).
- GDAL older than 3.13 cannot read the GeoZarr conventions (it still reads the CF `spatial_ref` metadata per level).
- OpenLayers uses a per-level resolution derived from the extent, which can differ slightly from the padded level
  resolution; see the
  [OpenLayers research note](https://github.com/lukegre/geozarr-pyramid-maker/blob/main/docs/research/03-openlayers-geozarr.md).
- Bool variables without a declared fill keep `False` as fill (no sentinel is possible).
