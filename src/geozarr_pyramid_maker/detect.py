"""Grid detection and normalisation (PLAN §4.1).

``detect`` inspects an xarray object, works out the spatial dims, the CRS and the affine
transform, and returns a normalised, still-lazy dataset: north-up, longitudes in -180..180,
float fill values masked to NaN, and every kept variable ordered ``(..., y, x)``.

Nothing here computes dask data variables; only coordinate values are read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyproj
import xarray as xr
from loguru import logger

_REL_TOL = 1e-6
_NAME_PAIRS = (("x", "y"), ("lon", "lat"), ("longitude", "latitude"))
_STD_NAME_PAIRS = (
    ("projection_x_coordinate", "projection_y_coordinate"),
    ("longitude", "latitude"),
    ("grid_longitude", "grid_latitude"),
)
_LON_NAMES = {"lon", "longitude"}
_LAT_NAMES = {"lat", "latitude"}
_FILL_KEYS = ("_FillValue", "missing_value")
_CURVILINEAR_MSG = "curvilinear or irregular grids are not supported in v0.1"


class DetectionError(ValueError):
    """Raised when the grid cannot be detected or is not supported."""


@dataclass(frozen=True)
class VarInfo:
    name: str
    dtype: np.dtype
    dims: tuple[str, ...]  # non-spatial dims first, then (y, x)
    shape: tuple[int, ...]
    categorical: bool  # int/bool dtype, or CF flag_values/flag_meanings attrs (D-14)
    # effective on-disk fill (D-20): floats NaN; ints the declared fill or a sentinel; bool False
    fill_value: int | float | bool | None = None
    fill_declared: bool = False  # True if the input declared a _FillValue/missing_value/nodata


@dataclass(frozen=True)
class GridInfo:
    x_dim: str
    y_dim: str
    crs: pyproj.CRS
    shape: tuple[int, int]  # (ny, nx)
    # affine (a, b, c, d, e, f), GDAL/rasterio order; c, f = outer top-left corner; e < 0
    transform: tuple[float, float, float, float, float, float]
    variables: tuple[VarInfo, ...]
    extra_dims: dict[str, int]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax)."""
        ny, nx = self.shape
        a, _, c, _, e, f = self.transform
        x1 = c + a * nx
        y1 = f + e * ny
        return (min(c, x1), min(f, y1), max(c, x1), max(f, y1))


# --------------------------------------------------------------------------- helpers


def _find_spatial_names(ds: xr.Dataset) -> tuple[str, str]:
    names = set(ds.variables)
    for xn, yn in _NAME_PAIRS:
        if xn in names and yn in names:
            return xn, yn
    xs = [n for n in ds.coords if str(ds[n].attrs.get("axis", "")).upper() == "X"]
    ys = [n for n in ds.coords if str(ds[n].attrs.get("axis", "")).upper() == "Y"]
    if len(xs) == 1 and len(ys) == 1:
        return xs[0], ys[0]
    for sx, sy in _STD_NAME_PAIRS:
        xs = [n for n in ds.coords if ds[n].attrs.get("standard_name") == sx]
        ys = [n for n in ds.coords if ds[n].attrs.get("standard_name") == sy]
        if len(xs) == 1 and len(ys) == 1:
            return xs[0], ys[0]
    raise DetectionError(
        f"Could not find spatial dimensions. Dims present: {list(ds.dims)}. Looked for x/y, "
        "lon/lat, longitude/latitude, CF axis='X'/'Y' and standard_name coordinates. "
        "Rename your dims/coords, e.g. ds.rename({'col': 'x', 'row': 'y'})."
    )


def _first_fill(v: xr.DataArray) -> Any:
    for src in (v.attrs, v.encoding):
        for key in _FILL_KEYS:
            val = src.get(key)
            if val is not None:
                return np.asarray(val).ravel()[0].item()
    try:
        return v.rio.nodata
    except Exception:
        return None


def _grid_mapping_names(ds: xr.Dataset) -> set[str]:
    names: set[str] = set()
    for v in ds.data_vars.values():
        for src in (v.attrs, v.encoding):
            gm = src.get("grid_mapping")
            if isinstance(gm, str) and gm:
                names.add(gm.split(":")[0].strip())
    if "spatial_ref" in ds.variables:
        names.add("spatial_ref")
    return {n for n in names if n in ds.variables}


def _lonlat_named(ds: xr.Dataset, xn: str, yn: str) -> bool:
    if xn.lower() in _LON_NAMES and yn.lower() in _LAT_NAMES:
        return True
    return (
        ds[xn].attrs.get("standard_name") == "longitude"
        and ds[yn].attrs.get("standard_name") == "latitude"
    )


def _resolve_crs(
    ds: xr.Dataset, crs: Any, x_name: str, y_name: str, xv: np.ndarray, yv: np.ndarray
) -> pyproj.CRS:
    if crs is not None:
        return pyproj.CRS.from_user_input(crs)

    try:
        rio_crs = ds.rio.crs
    except Exception:
        rio_crs = None
    if rio_crs is not None:
        return pyproj.CRS.from_user_input(rio_crs)

    for name in sorted(_grid_mapping_names(ds)):
        try:
            return pyproj.CRS.from_cf(dict(ds[name].attrs))
        except Exception as exc:
            logger.debug(f"Could not build a CRS from grid-mapping variable {name!r}: {exc}")

    named = _lonlat_named(ds, x_name, y_name)
    units = (
        str(ds[x_name].attrs.get("units", "")).lower()
        + str(ds[y_name].attrs.get("units", "")).lower()
    )
    grid_std = str(ds[x_name].attrs.get("standard_name", "")).startswith("grid_")
    has_degrees = "degree" in units and not grid_std
    in_range = (
        xv.size > 0 and xv.min() >= -180 and xv.max() <= 360 and yv.min() >= -90 and yv.max() <= 90
    )
    if (named or has_degrees) and in_range:
        logger.warning(
            "No CRS found; inferred EPSG:4326 from lon/lat coordinates (pass crs= to override)"
        )
        return pyproj.CRS.from_epsg(4326)
    raise DetectionError(
        "Could not determine the CRS: no crs= argument, no rioxarray/spatial_ref CRS and no "
        "usable grid_mapping variable, and the coordinates do not look like lon/lat degrees. "
        "Pass crs=, e.g. detect(ds, crs='EPSG:3031')."
    )


def _grid_step(vals: np.ndarray) -> float:
    """Robust step estimate in float64: least-squares slope against the index.

    For exact grids this equals (last - first) / (n - 1); for float32-rounded coordinates
    it averages the rounding noise instead of inheriting the endpoints' error.
    """
    v = np.asarray(vals, dtype="float64")
    if v.size == 2:
        return float(v[1] - v[0])
    i = np.arange(v.size, dtype="float64")
    i -= i.mean()
    return float(np.dot(i, v - v.mean()) / np.dot(i, i))


def _check_regular(vals: np.ndarray, axis: str) -> None:
    """Compare positions against the ideal grid (errors do not compound)."""
    v = vals.astype("float64")
    step = _grid_step(v)
    eps = np.finfo(vals.dtype).eps if vals.dtype.kind == "f" else np.finfo("float64").eps
    tol = max(_REL_TOL * abs(step), 8 * eps * float(np.max(np.abs(v))))
    ideal = v[0] + np.arange(v.size) * step
    if step == 0 or np.max(np.abs(v - ideal)) > tol:
        raise DetectionError(f"Coordinate {axis!r} is not regularly spaced: {_CURVILINEAR_MSG}.")


def _single_pixel_res(ds: xr.Dataset, xn: str, yn: str) -> tuple[float, float]:
    def _get(name: str) -> float | None:
        for attrs in (ds[name].attrs, ds.attrs):
            res = attrs.get("res")
            if res is not None:
                return abs(float(np.asarray(res).ravel()[0]))
        return None

    rx, ry = _get(xn), _get(yn)
    if rx is None or ry is None:
        raise DetectionError(
            f"Dimension {xn!r} or {yn!r} has a single pixel, so the resolution cannot be "
            "inferred. Add a 'res' attribute to the coordinate (or dataset), e.g. "
            "ds['x'].attrs['res'] = 10.0."
        )
    return rx, ry


def _sentinel_fill(dtype: np.dtype, attrs: Mapping[str, Any], name: str) -> int:
    """Sentinel fill for an int variable without a declared fill (D-20).

    Signed ints use the dtype minimum, unsigned ints the maximum. If that value is a CF class in
    ``flag_values`` the other end of the range is used; if both are, an explicit ``_FillValue``
    is required.
    """
    info = np.iinfo(dtype)
    first, other = (info.min, info.max) if dtype.kind == "i" else (info.max, info.min)
    flags = attrs.get("flag_values")
    used = {int(f) for f in np.atleast_1d(flags).tolist()} if flags is not None else set()
    if int(first) not in used:
        return int(first)
    if int(other) not in used:
        return int(other)
    raise DetectionError(
        f"Variable {name!r} has no _FillValue and its flag_values use both ends of the {dtype} "
        f"range ({int(info.min)} and {int(info.max)}), so no sentinel is free. Set an explicit "
        "_FillValue attribute."
    )


def effective_fill(
    dtype: np.dtype, attrs: Mapping[str, Any], declared: Any, name: str = "?"
) -> tuple[int | float | bool, bool]:
    """``(fill, declared?)`` written as the zarr ``fill_value`` and hidden by viewers (D-20).

    Floats: NaN. Ints: the declared fill, else a sentinel (dtype min/max, see ``_sentinel_fill``).
    Bool: always False; Zarr bool cannot carry a sentinel, so a bool variable without a real
    mask cannot distinguish nodata from False (documented limitation).
    """
    dtype = np.dtype(dtype)
    if dtype.kind == "f":
        return float("nan"), declared is not None
    if dtype.kind == "b":
        return False, False
    if declared is not None:
        return int(declared), True
    return _sentinel_fill(dtype, attrs, name), False


# ---------------------------------------------------------------------------- longitude


def _wrap_longitude(
    ds: xr.Dataset, x_dim: str, fills: dict[str, Any]
) -> tuple[xr.Dataset, dict[str, Any]]:
    """Roll 0..360 longitudes to -180..180 (D-11), keeping everything lazy."""
    xv = ds[x_dim].values.astype("float64")
    dx = _grid_step(xv) if xv.size > 1 else None
    if xv.min() >= 180:
        logger.warning(
            f"Longitudes are all >= 180 degrees; shifted {x_dim!r} by -360 degrees to -180..180"
        )
        return ds.assign_coords({x_dim: xv - 360.0}), fills

    k = int(np.searchsorted(xv, 180.0, side="right"))  # first index with x > 180
    logger.warning(
        f"Longitudes are in 0..360 degrees; wrapped {x_dim!r} values > 180 by -360 and "
        "re-sorted to -180..180"
    )
    ds = ds.assign_coords({x_dim: np.where(xv > 180.0, xv - 360.0, xv)})
    ds = xr.concat(
        [ds.isel({x_dim: slice(k, None)}), ds.isel({x_dim: slice(0, k)})],
        dim=x_dim,
        data_vars="minimal",
        coords="minimal",
        compat="override",
        join="override",
    )
    if dx is None:
        return ds, fills

    # snap onto the regular grid (removes float noise from the -360 shift)
    xs = ds[x_dim].values.astype("float64")
    idx = (xs - xs[0]) / dx
    if np.max(np.abs(idx - np.rint(idx))) > 1e-3:
        raise DetectionError(
            "Longitudes do not stay on a common grid after wrapping 0..360 to -180..180 "
            f"(360 is not a multiple of the grid spacing {dx})."
        )
    idx = np.rint(idx).astype("int64")
    ds = ds.assign_coords({x_dim: xs[0] + dx * idx})
    n_full = int(idx[-1]) + 1
    if n_full == xs.size:
        return ds, fills

    fill_map: dict[str, Any] = {}
    for name, v in ds.data_vars.items():
        if v.dtype.kind == "f":
            fill_map[name] = np.nan
        elif v.dtype.kind == "b":
            fill_map[name] = False
        else:
            fv = fills.get(name)
            if fv is None:
                fv = _sentinel_fill(v.dtype, v.attrs, name)
                logger.warning(
                    f"Variable {name!r} has no fill value; using sentinel {fv} for the "
                    "antimeridian gap"
                )
            fill_map[name] = fv
    logger.warning(
        f"Regional grid crosses the antimeridian (180 degrees); inserted a gap of "
        f"{n_full - xs.size} column(s) filled with the fill value "
        f"(grid is now {n_full} columns wide)"
    )
    ds = ds.reindex({x_dim: xs[0] + dx * np.arange(n_full)}, method=None, fill_value=fill_map)
    return ds, fills


# ------------------------------------------------------------------------------ detect


def detect(ds: xr.Dataset | xr.DataArray, crs: Any = None) -> tuple[xr.Dataset, GridInfo]:
    """Return the normalised (still lazy) dataset and its GridInfo."""
    if isinstance(ds, xr.DataArray):
        ds = ds.to_dataset(name=ds.name if ds.name is not None else "data")
    else:
        ds = ds.copy()

    x_dim, y_dim = _find_spatial_names(ds)
    for n in (x_dim, y_dim):
        if ds[n].ndim != 1:
            raise DetectionError(f"Spatial coordinate {n!r} is {ds[n].ndim}-D: {_CURVILINEAR_MSG}.")
    # 1-D coordinate on a differently named dim: make the coordinate the dimension
    swap = {ds[n].dims[0]: n for n in (x_dim, y_dim) if ds[n].dims[0] != n}
    if swap:
        ds = ds.swap_dims(swap)

    xv = ds[x_dim].values
    yv = ds[y_dim].values
    if xv.size > 1:
        _check_regular(xv, x_dim)
    if yv.size > 1:
        _check_regular(yv, y_dim)

    crs_obj = _resolve_crs(ds, crs, x_dim, y_dim, xv, yv)

    # ---- variables
    gm_names = _grid_mapping_names(ds)
    kept: list[str] = []
    dropped: list[str] = []
    for name, v in ds.data_vars.items():
        if name in gm_names:
            continue
        (kept if {x_dim, y_dim} <= set(v.dims) else dropped).append(str(name))
    for name in sorted(gm_names):
        logger.debug(f"Dropping grid-mapping variable {name!r} (rewritten on output)")
    if dropped:
        logger.warning(
            f"Dropping variable(s) without both spatial dims ({y_dim!r}, {x_dim!r}): {dropped}"
        )
    if not kept:
        raise DetectionError(
            f"No data variable has both spatial dims ({y_dim!r}, {x_dim!r}); "
            f"variables present: {list(ds.data_vars)}."
        )

    fills: dict[str, Any] = {name: _first_fill(ds[name]) for name in kept}

    ds = ds.drop_vars(dropped + sorted(gm_names), errors="ignore")
    used_dims = {d for name in kept for d in ds[name].dims}
    ds = ds.drop_vars([c for c in ds.coords if c not in ds.dims and set(ds[c].dims) - used_dims])

    # ---- orientation
    if xv.size > 1 and xv[1] < xv[0]:
        raise DetectionError(
            f"{x_dim!r} is descending; this is not supported. Flip it first, e.g. "
            f"ds.isel({x_dim}=slice(None, None, -1))."
        )
    if yv.size > 1 and yv[1] > yv[0]:
        logger.warning(f"{y_dim!r} is ascending; flipped to descending (north-up)")
        ds = ds.isel({y_dim: slice(None, None, -1)})

    # ---- longitude 0..360
    if crs_obj.is_geographic and float(np.max(xv)) > 180.0:
        ds, fills = _wrap_longitude(ds, x_dim, fills)

    # ---- fill values, attrs cleanup, dimension order
    spatial = (y_dim, x_dim)
    var_infos: list[VarInfo] = []
    extra_dims: dict[str, int] = {}
    for name in kept:
        v = ds[name]
        fill = fills.get(name)
        attrs = {k: val for k, val in v.attrs.items() if k not in (*_FILL_KEYS, "grid_mapping")}
        enc = {k: val for k, val in v.encoding.items() if k not in (*_FILL_KEYS, "grid_mapping")}
        if v.dtype.kind == "f" and fill is not None and not np.isnan(fill):
            v = v.where(v != fill)
        rec_fill, declared = effective_fill(v.dtype, v.attrs, fill, str(name))
        if v.dtype.kind in "iu" and declared:
            attrs["_FillValue"] = rec_fill  # sentinels are never written as a CF _FillValue
        order = [d for d in v.dims if d not in spatial] + list(spatial)
        v = v.transpose(*order)
        v.attrs = attrs
        v.encoding = enc
        ds[name] = v

        for d in order[:-2]:
            extra_dims.setdefault(str(d), int(ds.sizes[d]))
        categorical = v.dtype.kind in "iub" or "flag_values" in attrs or "flag_meanings" in attrs
        var_infos.append(
            VarInfo(
                name=name,
                dtype=v.dtype,
                dims=tuple(str(d) for d in order),
                shape=tuple(int(s) for s in v.shape),
                categorical=categorical,
                fill_value=rec_fill,
                fill_declared=declared,
            )
        )
        logger.debug(
            f"var {name!r}: dtype={v.dtype} dims={tuple(order)} shape={tuple(v.shape)} "
            f"categorical={categorical} fill={rec_fill}"
        )

    # ---- transform (pixel centres -> outer corner)
    xs = ds[x_dim].values.astype("float64")
    ys = ds[y_dim].values.astype("float64")
    nx, ny = xs.size, ys.size
    if nx == 1 or ny == 1:
        rx, ry = _single_pixel_res(ds, x_dim, y_dim)
    dx = _grid_step(xs) if nx > 1 else rx
    e = _grid_step(ys) if ny > 1 else -ry
    transform = (dx, 0.0, float(xs[0] - dx / 2), 0.0, e, float(ys[0] - e / 2))

    info = GridInfo(
        x_dim=str(x_dim),
        y_dim=str(y_dim),
        crs=crs_obj,
        shape=(ny, nx),
        transform=transform,
        variables=tuple(var_infos),
        extra_dims=extra_dims,
    )
    epsg = crs_obj.to_epsg()
    logger.info(
        f"Detected grid: crs={'EPSG:' + str(epsg) if epsg else crs_obj.name}, "
        f"dims=({y_dim}, {x_dim}), shape={info.shape}, variables={[v.name for v in var_infos]}"
    )
    return ds, info
