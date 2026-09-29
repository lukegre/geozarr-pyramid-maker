"""Lazy 2x downsampling of regular-grid datasets (PLAN §4.3, D-06, D-09, D-14, D-18).

Odd edges are padded, never trimmed, so the output shape is ``(ceil(ny/2), ceil(nx/2))`` and
the grid origin does not move. Everything stays lazy (dask); nothing is computed here.
"""

from __future__ import annotations

from collections.abc import Mapping

import dask.array as da
import numpy as np
import xarray as xr
from loguru import logger

from geozarr_pyramid_maker.detect import _grid_step

METHODS = ("mean", "nearest", "mode", "min", "max")


def metadata_method_name(method: str) -> str:
    """Name written to ``resampling_method`` metadata (D-18): ``mean`` -> ``average``."""
    return "average" if method == "mean" else method


def _new_coord(coord: xr.DataArray, *, descending: bool) -> xr.Variable:
    """Block-centre coordinate for the halved axis (float64), including the padded edge."""
    v = np.asarray(coord.values, dtype="float64")
    n = v.size
    if n > 1:
        step = _grid_step(v)
    else:
        res = coord.attrs.get("res")
        step = abs(float(np.asarray(res).ravel()[0])) if res is not None else 0.0
        step = -step if descending else step  # single pixel: y is north-up (descending)
    m = -(-n // 2)
    new = v[0] - step / 2 + (2 * np.arange(m) + 1) * step
    return xr.Variable(coord.dims, new, dict(coord.attrs))


def _is_nan_or_fill(a, fill):
    """Boolean array marking the values ignored in a vote (NaN and/or the fill value)."""
    if a.dtype.kind == "f":
        bad = da.isnan(a)
        if fill is not None and not np.isnan(fill):
            bad = bad | (a == fill)
        return bad
    if fill is None:
        return da.zeros(a.shape, chunks=a.chunks, dtype=bool)
    return a == fill


def _out_fill(dtype: np.dtype, fill):
    if dtype.kind == "f":
        return np.nan
    if dtype.kind == "b":
        return bool(fill) if fill is not None else False
    return fill if fill is not None else 0


def _mode_block(a, valid, *, fill):
    """2x2 block mode on blocks with even spatial sizes. ``valid`` marks usable pixels."""
    *lead, ny, nx = a.shape
    shape = (*lead, ny // 2, 2, nx // 2, 2)

    def blocks(arr):
        r = arr.reshape(shape)
        r = np.moveaxis(r, (-3, -1), (-2, -1))  # (..., y/2, x/2, 2(dy), 2(dx))
        return r.reshape(*r.shape[:-2], 4)  # order: TL, TR, BL, BR

    b, v = blocks(a), blocks(valid)
    counts = np.zeros(b.shape, dtype="int8")
    for k in range(4):
        for m in range(4):
            counts[..., k] += v[..., m] & (b[..., m] == b[..., k])
    counts = np.where(v, counts, -1)
    idx = counts.argmax(axis=-1)  # first maximum wins: TL, TR, BL, BR
    out = np.take_along_axis(b, idx[..., None], axis=-1)[..., 0]
    none_valid = ~v.any(axis=-1)
    fill_out = _out_fill(a.dtype, fill)
    return np.where(none_valid, np.asarray(fill_out, dtype=a.dtype), out).astype(a.dtype)


def _even_chunks(chunks: tuple[int, ...], total: int) -> tuple[int, ...]:
    bounds = np.cumsum(chunks)
    bounds = np.unique(np.minimum(bounds + bounds % 2, total))
    return tuple(int(c) for c in np.diff(np.concatenate([[0], bounds])))


def _pad_to_even(arr: da.Array, axis: int) -> da.Array:
    if arr.shape[axis] % 2 == 0:
        return arr
    width = [(0, 0)] * arr.ndim
    width[axis] = (0, 1)
    return da.pad(arr, width, mode="constant", constant_values=0)


def _mode(data: da.Array, fill) -> da.Array:
    ay, ax = data.ndim - 2, data.ndim - 1
    valid = ~_is_nan_or_fill(data, fill)  # lazy elementwise; padded pixels are False
    data, valid = _pad_to_even(data, ay), _pad_to_even(valid, ay)
    data, valid = _pad_to_even(data, ax), _pad_to_even(valid, ax)
    # padding valid with 0 == False, so padded pixels never vote
    spec = {
        ay: _even_chunks(data.chunks[ay], data.shape[ay]),
        ax: _even_chunks(data.chunks[ax], data.shape[ax]),
    }
    data, valid = data.rechunk(spec), valid.rechunk(spec)
    chunks = tuple(c if i < ay else tuple(s // 2 for s in c) for i, c in enumerate(data.chunks))
    return da.map_blocks(
        _mode_block, data, valid, fill=fill, dtype=data.dtype, chunks=chunks, meta=data._meta
    )


def _coarsen_reduce(data: da.Array, method: str, fill) -> da.Array:
    dtype = data.dtype
    ay, ax = data.ndim - 2, data.ndim - 1
    dims = tuple(f"d{i}" for i in range(data.ndim))
    is_float = dtype.kind == "f"
    if not is_float:
        work = data.astype("float64")
        if not (dtype.kind == "b" or fill is None):
            work = da.where(data == fill, np.nan, work)
    else:
        work = data
    arr = xr.DataArray(work, dims=dims)
    coarse = arr.coarsen({dims[ay]: 2, dims[ax]: 2}, boundary="pad")
    res = getattr(coarse, method)(skipna=True).data
    if is_float:
        return res.astype(dtype)
    if method == "mean":
        res = da.rint(res)  # round half to even
    res = da.where(da.isnan(res), _out_fill(dtype, fill), res)
    return res.astype(dtype)


def downsample(
    ds: xr.Dataset,
    *,
    x_dim: str,
    y_dim: str,
    methods: Mapping[str, str],
    fill_values: Mapping[str, int | float | None],
) -> xr.Dataset:
    """Halve the spatial resolution: output spatial shape is (ceil(ny/2), ceil(nx/2)).

    Odd edges are padded, never trimmed. Lazy; output dtype equals input dtype.
    """
    spatial = {x_dim, y_dim}
    names = [n for n, v in ds.data_vars.items() if spatial <= set(v.dims)]
    for n in names:
        if n not in methods:
            raise ValueError(f"No resampling method given for variable {n!r}")
        if methods[n] not in METHODS:
            raise ValueError(
                f"Unknown resampling method {methods[n]!r} for {n!r}; choose from {METHODS}"
            )

    new_x, new_y = _new_coord(ds[x_dim], descending=False), _new_coord(ds[y_dim], descending=True)
    coords: dict[str, xr.Variable] = {}
    for name, c in ds.coords.items():
        if name == x_dim:
            coords[name] = new_x
        elif name == y_dim:
            coords[name] = new_y
        elif not (spatial & set(c.dims)):
            coords[name] = xr.Variable(c.dims, c.variable.data, dict(c.attrs))

    out_vars: dict[str, xr.Variable] = {}
    for name, var in ds.data_vars.items():
        if name not in names:
            out_vars[name] = xr.Variable(var.dims, var.variable.data, dict(var.attrs))
            continue
        method, fill = methods[name], fill_values.get(name)
        var = var.transpose(..., y_dim, x_dim)
        data = var.data if isinstance(var.data, da.Array) else da.from_array(var.data)
        logger.trace(
            f"downsample {name!r}: method={method} shape={data.shape} chunks={data.chunks}"
        )
        if method == "nearest":
            res = data[..., ::2, ::2]
        elif method == "mode":
            res = _mode(data, fill)
        else:
            res = _coarsen_reduce(data, method, fill)
        attrs = dict(var.attrs)
        attrs["resampling_method"] = metadata_method_name(method)
        out_vars[name] = xr.Variable(var.dims, res, attrs)

    return xr.Dataset(out_vars, coords=coords, attrs=dict(ds.attrs))
