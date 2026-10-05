# geozarr-pyramid-maker

!!! danger "Experimental"
    This project is an **experimental approach** to building GeoZarr multiscale pyramids. Treat everything as
    unstable and not production-ready.

    - **APIs, output layout and the viewer can change without notice.** There are no stability guarantees.
    - **GeoZarr conventions are still evolving.** Output may stop matching the spec or other tools.
    - **No reprojection.** Pyramid levels stay in the source CRS; there is no WebMercator tiling.
    - **Regular grids only.** Curvilinear and irregular grids are not supported.
    - **GDAL older than 3.13** cannot read the GeoZarr conventions (CF `spatial_ref` metadata still works).
    - **The viewer needs CORS and HTTP Range** on remote stores. Many buckets do not provide them.

Turn any regular-grid xarray Dataset (NetCDF, Zarr, GeoTIFF) into a **GeoZarr multiscale pyramid**: Zarr v3, sharded,
in the native CRS, with the modular GeoZarr conventions and CF `grid_mapping` metadata. One call, no required options:
`ds.geozarr.to_pyramid("out.zarr")`. Everything is lazy with dask, so data larger than memory is fine. The result
is meant for web viewers (OpenLayers, TiTiler, ...) and still opens with xarray, GDAL and QGIS.

[Open the viewer](viewer/){ .md-button .md-button--primary }
[Get started](usage.md){ .md-button }

## Credit

The original code and idea come from the ESA Polar Hackathon team:
[GeoZarr conversion](https://esa-earthcode.github.io/polar_hackathon/geozarr-conversion/).

## Source

Code, issues and the Docker image definition live at
[github.com/lukegre/geozarr-pyramid-maker](https://github.com/lukegre/geozarr-pyramid-maker). MIT licensed.
