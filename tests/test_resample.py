"""Tests for resample.downsample (PLAN §4.3), against pure-NumPy reference implementations."""

from __future__ import annotations

import math

import dask
import numpy as np
import pytest
import xarray as xr

from geozarr_pyramid_maker.detect import detect
from geozarr_pyramid_maker.resample import downsample, metadata_method_name

X, Y = "x", "y"


def _ds(arr, dims=None, chunks=None, dx=10.0, dy=-10.0, x0=105.0, y0=995.0, attrs=None):
    arr = np.asarray(arr)
    dims = dims or (("y", "x") if arr.ndim == 2 else ("time", "y", "x"))
    ny, nx = arr.shape[-2:]
    coords = {"x": x0 + dx * np.arange(nx), "y": y0 + dy * np.arange(ny)}
    if "time" in dims:
        coords["time"] = np.arange(arr.shape[0]).astype("datetime64[D]")
    da = xr.DataArray(arr, dims=dims, coords=coords, attrs=attrs or {})
    ds = xr.Dataset({"v": da}, attrs={"title": "t"})
    return ds.chunk(chunks) if chunks else ds.chunk()


def _run(ds, method, fill=None):
    return downsample(ds, x_dim=X, y_dim=Y, methods={"v": method}, fill_values={"v": fill})


def _ref(a, method, fill):
    """Plain-loop reference with padding semantics (pad pixels simply do not exist)."""
    kind = a.dtype.kind
    ny, nx = a.shape[-2:]
    oy, ox = -(-ny // 2), -(-nx // 2)
    out_fill = np.nan if kind == "f" else (0 if fill is None else fill)
    out = np.empty((*a.shape[:-2], oy, ox), dtype="float64" if kind == "f" else a.dtype)
    for idx in np.ndindex(*a.shape[:-2]):
        plane = a[idx]
        for j in range(oy):
            for i in range(ox):
                blk = plane[2 * j : 2 * j + 2, 2 * i : 2 * i + 2].ravel()  # TL, TR, BL, BR
                if kind == "f":
                    valid = [v for v in blk if not np.isnan(v)]
                elif fill is None:
                    valid = list(blk)
                else:
                    valid = [v for v in blk if v != fill]
                if method == "nearest":
                    r = blk[0]
                elif not valid:
                    r = out_fill
                elif method == "mean":
                    m = np.mean(np.asarray(valid, dtype="float64"))
                    r = m if kind == "f" else np.rint(m)
                elif method == "min":
                    r = min(valid)
                elif method == "max":
                    r = max(valid)
                else:  # mode: first-seen wins ties
                    best, bc = None, 0
                    for v in valid:
                        c = sum(1 for w in valid if w == v)
                        if c > bc:
                            best, bc = v, c
                    r = best
                out[(*idx, j, i)] = r
    return out.astype(a.dtype)


def _rand(shape, dtype, seed=0, nan=False):
    rng = np.random.default_rng(seed)
    if np.dtype(dtype).kind == "f":
        a = rng.normal(size=shape).astype(dtype)
        if nan:
            a[rng.random(shape) < 0.3] = np.nan
        return a
    if np.dtype(dtype).kind == "b":
        return rng.random(shape) < 0.5
    info = np.iinfo(dtype)
    return rng.integers(max(info.min, -3), min(info.max, 4), size=shape).astype(dtype)


SHAPES = [(7, 5), (8, 6), (1, 1), (1, 9), (3, 1)]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("method", ["mean", "min", "max", "nearest", "mode"])
@pytest.mark.parametrize("nan", [False, True])
def test_float_matches_reference(shape, method, nan):
    a = _rand(shape, "float64", nan=nan)
    out = _run(_ds(a), method, np.nan)
    assert out.v.shape == (-(-shape[0] // 2), -(-shape[1] // 2))
    np.testing.assert_allclose(out.v.values, _ref(a, method, np.nan), equal_nan=True)


@pytest.mark.parametrize("method", ["mean", "min", "max", "nearest", "mode"])
@pytest.mark.parametrize("chunks", [{"y": 3, "x": 3}, {"y": 1, "x": 2}, {"y": 5, "x": 4}])
def test_time_dim_and_odd_chunks(method, chunks):
    a = _rand((3, 7, 9), "float32", nan=True)
    out = _run(_ds(a, chunks={"time": 1, **chunks}), method, np.nan)
    expected = _ref(a, method, np.nan)
    assert out.v.dims == ("time", "y", "x")
    np.testing.assert_allclose(out.v.values, expected, rtol=1e-6, equal_nan=True)
    assert out.v.data.chunks[0] == (1, 1, 1)  # non-spatial chunking preserved for mode/mean


@pytest.mark.parametrize("dtype", ["int8", "uint8", "int32", "uint16"])
@pytest.mark.parametrize("method", ["mean", "min", "max", "nearest", "mode"])
@pytest.mark.parametrize("fill", [None, 0, 2])
def test_int_matches_reference(dtype, method, fill):
    a = _rand((7, 5), dtype, seed=3)
    out = _run(_ds(a, chunks={"y": 3, "x": 3}), method, fill)
    assert out.v.dtype == np.dtype(dtype)
    np.testing.assert_array_equal(out.v.values, _ref(a, method, fill))


@pytest.mark.parametrize("method", ["mean", "min", "max", "nearest", "mode"])
def test_bool(method):
    a = _rand((7, 5), "bool", seed=4)
    out = _run(_ds(a, chunks={"y": 3, "x": 3}), method, None)
    assert out.v.dtype == np.dtype("bool")
    np.testing.assert_array_equal(out.v.values, _ref(a, method, None))


def test_mean_rounds_half_to_even():
    a = np.array([[1, 2], [2, 2]], dtype="int16")  # mean 1.75 -> 2
    b = np.array([[1, 2], [1, 2]], dtype="int16")  # mean 1.5 -> 2
    c = np.array([[0, 1], [1, 2]], dtype="int16")  # 1.0
    d = np.array([[0, 1], [0, 1]], dtype="int16")  # 0.5 -> 0
    for arr, exp in [(a, 2), (b, 2), (c, 1), (d, 0)]:
        assert _run(_ds(arr), "mean", None).v.values[0, 0] == exp


def test_mean_int_ignores_fill():
    a = np.array([[-1, 4], [6, -1]], dtype="int16")
    assert _run(_ds(a), "mean", -1).v.values[0, 0] == 5
    a = np.full((2, 2), -1, dtype="int16")
    assert _run(_ds(a), "mean", -1).v.values[0, 0] == -1
    assert _run(_ds(np.zeros((2, 2), "int16")), "mean", None).v.values[0, 0] == 0


# ------------------------------------------------------------------ mode specifics


def test_mode_ties_go_to_first_in_block_order():
    # TL=1 TR=2 BL=2 BR=1 -> tie, TL wins
    a = np.array([[1, 2], [2, 1]], dtype="int8")
    assert _run(_ds(a), "mode", None).v.values[0, 0] == 1
    # TL=3 TR=1 BL=2 BR=2 -> 2 wins the vote
    a = np.array([[3, 1], [2, 2]], dtype="int8")
    assert _run(_ds(a), "mode", None).v.values[0, 0] == 2
    # 4 distinct -> TL
    a = np.array([[5, 6], [7, 8]], dtype="uint8")
    assert _run(_ds(a), "mode", None).v.values[0, 0] == 5
    # TL fill; TR=2 BL=3 -> tie, TR first
    a = np.array([[-1, 2], [3, -1]], dtype="int8")
    assert _run(_ds(a), "mode", -1).v.values[0, 0] == 2


def test_mode_ignores_fill_values():
    a = np.array([[-1, -1], [-1, 7]], dtype="int8")  # fill is most common but ignored
    assert _run(_ds(a), "mode", -1).v.values[0, 0] == 7


def test_mode_all_fill_gives_fill():
    a = np.full((4, 4), -9, dtype="int32")
    a[2:, 2:] = 3
    out = _run(_ds(a), "mode", -9).v.values
    np.testing.assert_array_equal(out, [[-9, -9], [-9, 3]])


def test_mode_all_fill_none_gives_zero_for_padded_edge_only_blocks():
    a = np.array([[4, 4, 5]], dtype="uint8")
    np.testing.assert_array_equal(_run(_ds(a), "mode", None).v.values, [[4, 5]])


def test_mode_fill_equal_to_padding_sentinel_does_not_leak():
    # fill 0 present in data and odd edges: edge block of a single pixel = that pixel
    a = np.array([[1, 1, 2], [1, 3, 0], [9, 9, 9]], dtype="int8")
    out = _run(_ds(a), "mode", 0).v.values
    np.testing.assert_array_equal(out, [[1, 2], [9, 9]])


def test_mode_float_nan_is_fill():
    a = np.array([[np.nan, np.nan], [np.nan, np.nan]])
    assert np.isnan(_run(_ds(a), "mode", np.nan).v.values[0, 0])
    a = np.array([[np.nan, 2.0], [np.nan, 3.0]])
    assert _run(_ds(a), "mode", np.nan).v.values[0, 0] == 2.0


def test_mode_chunks_even_and_lazy():
    a = _rand((11, 13), "int8")
    out = _run(_ds(a, chunks={"y": 3, "x": 5}), "mode", None)
    assert isinstance(out.v.data, dask.array.Array)
    np.testing.assert_array_equal(out.v.values, _ref(a, "mode", None))


# ------------------------------------------------------------------ general behaviour


@pytest.mark.parametrize("dtype", ["float32", "float64", "int8", "uint16"])
@pytest.mark.parametrize("method", ["mean", "min", "max", "nearest", "mode"])
def test_dtype_preserved(dtype, method):
    a = _rand((6, 6), dtype)
    fill = np.nan if np.dtype(dtype).kind == "f" else None
    out = _run(_ds(a, chunks={"y": 3, "x": 3}), method, fill)
    assert out.v.dtype == np.dtype(dtype)
    assert out.v.compute().dtype == np.dtype(dtype)


@pytest.mark.parametrize("n", [1, 2, 7, 8])
def test_coords_exact_including_padded_edge(n):
    a = _rand((n, n), "float64")
    x0, y0, dx, dy = 105.0, 995.0, 10.0, -10.0
    ds = _ds(a, dx=dx, dy=dy, x0=x0, y0=y0, attrs={})
    ds = ds.assign_coords(x=ds.x.assign_attrs(res=10.0), y=ds.y.assign_attrs(res=10.0))
    out = _run(ds, "mean", np.nan)
    m = -(-n // 2)
    i = np.arange(m)
    np.testing.assert_allclose(out.x.values, x0 - dx / 2 + (2 * i + 1) * dx, rtol=0, atol=1e-9)
    np.testing.assert_allclose(out.y.values, y0 - dy / 2 + (2 * i + 1) * dy, rtol=0, atol=1e-9)
    assert out.x.dtype == np.float64 and out.y.dtype == np.float64


def test_coords_from_float32_source_are_float64():
    n = 8
    ds = _ds(_rand((n, n), "float32"))
    ds = ds.assign_coords(x=ds.x.astype("float32"), y=ds.y.astype("float32"))
    out = _run(ds, "mean", np.nan)
    assert out.x.dtype == np.float64
    np.testing.assert_allclose(out.x.values, 105.0 - 5 + (2 * np.arange(4) + 1) * 10, rtol=1e-6)


def test_nonspatial_coords_pass_through():
    a = _rand((3, 6, 6), "float32")
    ds = _ds(a)
    out = _run(ds, "mean", np.nan)
    np.testing.assert_array_equal(out.time.values, ds.time.values)


def test_attrs_and_method_metadata():
    a = _rand((4, 4), "float32")
    ds = _ds(a, attrs={"units": "m/yr"})
    ds.v.encoding["chunks"] = (2, 2)
    out = _run(ds, "mean", np.nan)
    assert out.attrs["title"] == "t"
    assert out.v.attrs["units"] == "m/yr"
    assert out.v.attrs["resampling_method"] == "average"
    assert out.v.encoding == {}
    assert _run(ds, "nearest", np.nan).v.attrs["resampling_method"] == "nearest"
    assert ds.v.attrs.get("resampling_method") is None  # input not mutated


def test_metadata_method_name():
    assert metadata_method_name("mean") == "average"
    for m in ("nearest", "mode", "min", "max"):
        assert metadata_method_name(m) == m


def test_errors():
    ds = _ds(_rand((4, 4), "float32"))
    with pytest.raises(ValueError, match="bilinear"):
        _run(ds, "bilinear", np.nan)
    with pytest.raises(ValueError, match="v"):
        downsample(ds, x_dim=X, y_dim=Y, methods={}, fill_values={})


@pytest.mark.parametrize("method", ["mean", "min", "max", "nearest", "mode"])
def test_lazy_no_compute(method):
    a = _rand((3, 9, 7), "int8" if method == "mode" else "float32", nan=method != "mode")
    ds = _ds(a, chunks={"time": 1, "y": 3, "x": 3})

    def boom(*args, **kwargs):
        raise AssertionError("compute called")

    with dask.config.set(scheduler=boom):
        out = _run(ds, method, None if method == "mode" else np.nan)
    assert isinstance(out.v.data, dask.array.Array)
    np.testing.assert_allclose(
        out.v.values.astype("float64"),
        _ref(a, method, None if method == "mode" else np.nan).astype("float64"),
        rtol=1e-6,
        equal_nan=True,
    )


@pytest.mark.parametrize("method", ["mean", "mode", "nearest"])
def test_chain_twice(method):
    n = (13, 9)
    a = _rand(n, "float32")
    out = _run(_run(_ds(a, chunks={"y": 3, "x": 3}), method, np.nan), method, np.nan)
    assert out.v.shape == tuple(math.ceil(math.ceil(k / 2) / 2) for k in n)
    assert out.v.shape == (4, 3)
    assert out.v.compute().shape == (4, 3)


def test_multi_var_after_detect(multi_var):
    ds, grid = detect(multi_var)
    info = {v.name: v for v in grid.variables}
    ds = ds[["melt", "mask"]]
    out = downsample(
        ds,
        x_dim=grid.x_dim,
        y_dim=grid.y_dim,
        methods={"melt": "mean", "mask": "mode"},
        fill_values={n: info[n].fill_value for n in ("melt", "mask")},
    )
    assert out.melt.shape == (3, 30, 25)
    assert out.mask.shape == (30, 25)
    assert out.mask.dtype == np.dtype("int8")
    assert out.melt.attrs["resampling_method"] == "average"
    assert out.mask.attrs["resampling_method"] == "mode"
    assert out.mask.attrs["flag_values"] == [0, 1, 2]
    m = out.mask.values
    assert m[0, 0] in (0, 1, 2)  # fill (-1) at [0,0] ignored in the vote
    assert out.melt.dtype == np.dtype("float32")
