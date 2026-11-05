"""Tests for bit rounding per level (D-60)."""

import numpy as np
import pytest
import xarray as xr

from geozarr_pyramid_maker.detect import detect
from geozarr_pyramid_maker.resample import downsample
from geozarr_pyramid_maker.write import _bitround, to_pyramid

FILL = -9999.5


def _make(dtype="float32", fill=None, n=64):
    rng = np.random.default_rng(0)
    data = (rng.random((n, n)) * 1000 + 1).astype(dtype)
    if fill is not None:
        data[3, 5] = fill
    else:
        data[7, 9] = np.nan
    lon = -180.0 + 5.625 * (np.arange(n) + 0.5)
    lat = 90.0 - 2.8125 * (np.arange(n) + 0.5)
    attrs = {"_FillValue": fill} if fill is not None else {}
    ds = xr.Dataset({"t": (("lat", "lon"), data, attrs)}, coords={"lon": lon, "lat": lat})
    ds["count"] = (("lat", "lon"), np.ones((n, n), dtype="int16"))
    return ds.rio.write_crs("EPSG:4326")


def _level(path, k):
    return xr.open_zarr(path, group=str(k), consolidated=False, mask_and_scale=False)


def _low_bits(a, keepbits):
    bits = np.ascontiguousarray(a, dtype="float32").view(np.uint32)
    return bits & np.uint32((1 << (23 - keepbits)) - 1)


@pytest.mark.parametrize("dtype,keepbits", [("float32", 5), ("float64", 12)])
def test_error_bound_and_nan_mask(dtype, keepbits, tmp_path):
    ds = _make(dtype)
    res = to_pyramid(ds, str(tmp_path / "o.zarr"), tile_size=16, keepbits=keepbits)
    assert len(res.plan.levels) > 1
    got = _level(tmp_path / "o.zarr", 0).t.values
    assert got.dtype == np.dtype(dtype)
    orig = ds.t.values
    np.testing.assert_array_equal(np.isnan(got), np.isnan(orig))
    ok = ~np.isnan(orig)
    assert np.max(np.abs(got[ok] - orig[ok]) / np.abs(orig[ok])) <= 2.0 ** -(keepbits + 1)
    assert np.any(got[ok] != orig[ok])


def test_low_bits_zero_at_every_level_and_attr(tmp_path):
    res = to_pyramid(_make(), str(tmp_path / "o.zarr"), tile_size=16, keepbits=6)
    for k in range(len(res.plan.levels)):
        lvl = _level(tmp_path / "o.zarr", k)
        assert lvl.t.attrs["bitround_keepbits"] == 6
        assert "bitround_keepbits" not in lvl["count"].attrs
        vals = lvl.t.values
        assert not _low_bits(vals[~np.isnan(vals)], 6).any()


def test_declared_fill_stays_masked(tmp_path):
    # detect masks a declared float fill to NaN (D-20), which rounding must keep
    to_pyramid(_make(fill=FILL), str(tmp_path / "o.zarr"), tile_size=16, keepbits=3)
    got = _level(tmp_path / "o.zarr", 0).t.values
    assert np.isnan(got[3, 5]) and np.count_nonzero(np.isnan(got)) == 1


def test_bitround_helper_fill_unchanged():
    da = xr.DataArray(np.array([FILL, 1.2345678, np.nan], dtype="float32"))
    out = _bitround(da, 2, FILL).values
    assert out[0] == np.float32(FILL) and np.isnan(out[2]) and out[1] != np.float32(1.2345678)


def test_numpy_and_dask_identical(tmp_path):
    ds = _make()
    to_pyramid(ds, str(tmp_path / "a.zarr"), tile_size=16, keepbits=7)
    to_pyramid(ds.chunk({"lat": 16, "lon": 16}), str(tmp_path / "b.zarr"), tile_size=16, keepbits=7)
    for k in range(3):
        a, b = _level(tmp_path / "a.zarr", k).t.values, _level(tmp_path / "b.zarr", k).t.values
        assert a.tobytes() == b.tobytes()


def test_level1_is_rounded_mean_of_rounded_level0(tmp_path):
    ds = _make()
    res = to_pyramid(ds, str(tmp_path / "o.zarr"), tile_size=16, keepbits=4)
    lvl0 = _level(tmp_path / "o.zarr", 0).drop_vars("spatial_ref", errors="ignore").compute()
    norm, grid = detect(lvl0, crs="EPSG:4326")
    fills = {v.name: v.fill_value for v in grid.variables}
    mean = downsample(
        norm, x_dim=grid.x_dim, y_dim=grid.y_dim, methods=res.plan.resampling, fill_values=fills
    ).compute()
    expected = _bitround(mean.t, 4, np.nan).values
    np.testing.assert_array_equal(_level(tmp_path / "o.zarr", 1).t.values, expected)


def test_int_var_skipped_and_mapping(tmp_path):
    res = to_pyramid(_make(), str(tmp_path / "o.zarr"), tile_size=16, keepbits=4)
    assert dict(res.plan.keepbits) == {"t": 4}
    assert "keepbits 4" in repr(res.plan)
    res = to_pyramid(_make(), str(tmp_path / "p.zarr"), tile_size=16, keepbits={"t": 9})
    assert dict(res.plan.keepbits) == {"t": 9}


@pytest.mark.parametrize("keepbits", [{"count": 3}, {"nope": 3}, 24, -1, {"t": 24}, 2.5, True])
def test_invalid_keepbits_raise_before_writing(keepbits, tmp_path):
    with pytest.raises(ValueError):
        to_pyramid(_make(), str(tmp_path / "o.zarr"), tile_size=16, keepbits=keepbits)
    assert not (tmp_path / "o.zarr").exists()


def test_float64_range_allows_52(tmp_path):
    to_pyramid(_make("float64"), str(tmp_path / "o.zarr"), tile_size=16, keepbits=52)
    with pytest.raises(ValueError):
        to_pyramid(_make("float64"), str(tmp_path / "p.zarr"), tile_size=16, keepbits=53)


def test_cli_parse_keepbits():
    from geozarr_pyramid_maker.cli import _parse_keepbits

    assert _parse_keepbits(None) is None
    assert _parse_keepbits(["8"]) == 8
    assert _parse_keepbits(["a=8", "b=3"]) == {"a": 8, "b": 3}
    for bad in (["8", "a=3"], ["1", "2"], ["x"], ["a="]):
        with pytest.raises(ValueError):
            _parse_keepbits(bad)
