# OpenLayers GeoZarr source (verified 2026-09-29, ol 10.10.0)

Checked against the published `ol@10.10.0/source/GeoZarr.js`; `View.js` exports checked by fetching it.

## Loading
- `ol/source/GeoZarr.js` imports the bare specifier `zarrita`, so raw jsDelivr file URLs fail in a
  browser. Use the `+esm` bundles per module: `https://cdn.jsdelivr.net/npm/ol@10.10.0/<Module>.js/+esm`
  (`Map`, `View`, `layer/WebGLTile`, `layer/Tile`, `source/GeoZarr`, `source/OSM`, `proj/proj4`).
- CSS: `https://cdn.jsdelivr.net/npm/ol@10.10.0/ol.css`.
- proj4: `https://cdn.jsdelivr.net/npm/proj4@2.22.0/+esm` (2.22.0 is the latest 2.x on jsDelivr; the
  `+esm` URL returns 200). `preview.OL_VERSION` / `PROJ4_VERSION` hold the versions.

## Source
- `new GeoZarr({url, bands, projection?, transition?, wrapX?, resample?, dimensions?})`.
- `url` = multiscales group = our store root. `bands` = variable names; we use one variable per layer.
- `dimensions` = integer index per non-spatial dim (`{time: 0}`, default 0);
  `source.updateDimensions({...})` changes it live.
- Nodata comes from the array `fill_value` (NaN handled); nodata pixels get alpha 0 via an extra band.
- Tile grid per level: root `spatial:bbox` plus each layout entry's `spatial:shape`.

## Projection
- CRS is resolved from attrs: `proj:code` -> `getProjection(code)`, which is null unless registered
  (only EPSG:4326 and EPSG:3857 are built in); otherwise `proj:projjson` / `proj:wkt2` via proj4.
- The page therefore registers first: `proj4.defs(CODE, PROJ4); register(proj4)`.
  `PROJ4` comes from `pyproj.CRS.to_proj4()`; skipped for EPSG:4326/3857.
- A CRS without a code is registered as `GPM:custom` and passed as `projection: "GPM:custom"`.

## View
`View.js` (10.10.0) exports: `createCenterConstraint`, `createResolutionConstraint`,
`createRotationConstraint`, `isNoopAnimation`, default `View`, and the view-option helpers
`withHigherResolutions`, `withLowerResolutions`, `withExtentCenter`, `withZoom`, plus
`async getView(source, ...transforms)`. We use
`await getView(source, withLowerResolutions(1), withHigherResolutions(2), withExtentCenter(), withZoom(2))`.

## Style (WebGLTile)
- Continuous: `['interpolate', ['linear'], ['band', 1], vmin, [r,g,b,1], ..., vmax, [r,g,b,1]]`
  (viridis 5 stops, vmin/vmax = 2nd-98th percentile of the coarsest level).
- Categorical: `['match', ['band', 1], v1, c1, ..., fallback]` over the unique values (max 20).

## Serving
zarrita reads sharded stores by HTTP byte ranges, so the server must answer `Range` (206 /
`Content-Range`, 416) and send CORS headers, also on 404 (zarrita probes for missing `zarr.json`).
`http.server.SimpleHTTPRequestHandler` ignores `Range`; `preview.RangeHandler` implements it.

## Known limitation
OL derives each level's resolution as root bbox width / level `spatial:shape[1]`. We pad odd
edges when halving, so a level-k grid covers slightly more than the root bbox in reality; OL stretches
the coarse levels by less than one level-k pixel across the full extent. Visually negligible, but it is
a small georeferencing error at coarse zoom.
