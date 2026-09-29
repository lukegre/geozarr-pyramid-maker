"""Tests for D-20: integer sentinel fill values, padding and antimeridian gaps."""

import json
import math
from importlib import import_module

import numpy as np
import pytest
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
import zarr

from geozarr_pyramid_maker.detect import DetectionError, detect
from geozarr_pyramid_maker.metadata import VARIABLES_ATTR
from geozarr_pyramid_maker.write import to_pyramid

pv = import_module("geozarr_pyramid_maker.preview")


def _grid(data: np.ndarray, attrs: dict | None = None, name: str = "v") -> xr.Dataset:
    ny, nx = data.shape
    x = 500.0 + 1000.0 * np.arange(nx)
    y = 1000.0 * ny - 500.0 - 1000.0 * np.arange(ny)
    ds = xr.Dataset({name: (("y", "x"), data, attrs or {})}, coords={"x": x, "y": y})
    return ds.rio.write_crs("EPSG:3031")


def _levels(store) -> list[str]:
    layout = zarr.open_group(store, mode="r").attrs["multiscales"]["layout"]
    return [str(entry["asset"]) for entry in layout]


def _write(ds, tmp_path, tile_size=8, **kw):
    store = tmp_path / "d.zarr"
    to_pyramid(ds, store, tile_size=tile_size, validate=False, **kw)
    return store


def _disk_fill(store, level, name):
    meta = json.loads((store / level / name / "zarr.json").read_text())
    return meta["fill_value"]


def _mask_ds(dtype="int8", attrs=None, ny=40, nx=36) -> xr.Dataset:
    data = (np.arange(ny * nx).reshape(ny, nx) % 3).astype(dtype)
    return _grid(data, {"flag_values": [0, 1, 2]} if attrs is None else attrs, "mask")


# --------------------------------------------------------------------------- sentinels


def test_int8_mask_sentinel_on_disk_and_readback(tmp_path):
    ds = _mask_ds()
    store = _write(ds, tmp_path)
    levels = _levels(store)
    assert len(levels) >= 2
    for k in levels:
        assert _disk_fill(store, k, "mask") == -128
    n_zeros = int((ds["mask"].values == 0).sum())
    for k in levels:
        back = xr.open_dataset(store, group=k, engine="zarr")
        assert back["mask"].dtype == np.int8
        assert "_FillValue" not in back["mask"].attrs
        assert (
            "_FillValue" not in back["mask"].encoding or back["mask"].encoding["_FillValue"] is None
        )
        if k == levels[0]:
            assert int((back["mask"].values == 0).sum()) == n_zeros


def test_uint8_sentinel_255():
    info, _ = _detect_info(np.zeros((4, 4), "uint8"))
    assert info.fill_value == 255
    assert info.fill_declared is False


def test_uint8_sentinel_255_on_disk(tmp_path):
    store = _write(_grid((np.arange(64).reshape(8, 8) % 3).astype("uint8")), tmp_path, 4)
    assert _disk_fill(store, "0", "v") == 255


def test_flag_values_with_min_gives_max():
    attrs = {"flag_values": [-128, 0, 1]}
    info, _ = _detect_info(np.zeros((4, 4), "int8"), attrs)
    assert info.fill_value == 127


def test_flag_values_both_ends_raises():
    with pytest.raises(DetectionError):
        _detect_info(np.zeros((4, 4), "int8"), {"flag_values": [-128, 0, 127]})


def test_declared_fill_respected(tmp_path):
    data = (np.arange(64).reshape(8, 8) % 3).astype("int8")
    data[0, 0] = -1
    ds = _grid(data, {"_FillValue": -1})
    info, _ = _detect_info(data, {"_FillValue": -1})
    assert info.fill_value == -1
    assert info.fill_declared is True
    store = _write(ds, tmp_path, 4)
    for k in _levels(store):
        assert _disk_fill(store, k, "v") == -1


def test_bool_and_float_defaults():
    binfo, _ = _detect_info(np.zeros((4, 4), bool))
    assert binfo.fill_value is False
    assert binfo.fill_declared is False
    finfo, _ = _detect_info(np.zeros((4, 4), "float32"))
    assert math.isnan(finfo.fill_value)


def _detect_info(data, attrs=None):
    _ds, grid = detect(_grid(data, attrs))
    return next(v for v in grid.variables if v.name == "v"), grid


# --------------------------------------------------------------------------- padding


def _reference(data, method, sentinel):
    """Level-1 reference on a (7, 5) array ignoring padded pixels and sentinels."""
    ny, nx = data.shape
    out = np.full(((ny + 1) // 2, (nx + 1) // 2), sentinel, dtype=data.dtype)
    for i in range(out.shape[0]):
        for j in range(out.shape[1]):
            block = data[2 * i : 2 * i + 2, 2 * j : 2 * j + 2].ravel()
            block = block[block != sentinel]
            if block.size == 0:
                continue
            if method == "min":
                out[i, j] = block.min()
            elif method == "max":
                out[i, j] = block.max()
            elif method == "mean":
                out[i, j] = np.rint(block.astype("float64").mean())
            elif method == "mode":
                vals, counts = np.unique(block, return_counts=True)
                out[i, j] = vals[counts == counts.max()][0]  # tie handled by caller data
    return out


@pytest.mark.parametrize("method", ["mode", "min", "max", "mean"])
def test_odd_grid_padding_ignored(tmp_path, method):
    rng = np.random.default_rng(0)
    data = rng.integers(10, 100, size=(7, 5)).astype("int8")
    if method == "mode":
        # unique values per block so no ties; last row/col blocks have 1-2 real pixels
        data = np.arange(35, dtype="int8").reshape(7, 5) + 10
        data[:, 1] = data[:, 0]  # make duplicates so modes are meaningful
    data[0:2, 0:2] = -128  # an all-sentinel block stays sentinel
    store = _write(_grid(data), tmp_path, tile_size=4, resampling=method)
    levels = _levels(store)
    lvl1 = xr.open_dataset(store, group=levels[1], engine="zarr")["v"].values
    assert lvl1.shape == (4, 3)
    assert lvl1.dtype == np.int8
    ref = _reference(data, method, -128)
    if method == "mode":
        # ties resolved by scan order in the implementation; check membership instead
        for i in range(4):
            for j in range(3):
                block = data[2 * i : 2 * i + 2, 2 * j : 2 * j + 2].ravel()
                block = block[block != -128]
                if block.size == 0:
                    assert lvl1[i, j] == -128
                else:
                    vals, counts = np.unique(block, return_counts=True)
                    assert lvl1[i, j] in vals[counts == counts.max()]
    else:
        np.testing.assert_array_equal(lvl1, ref)
    assert lvl1[0, 0] == -128
    # last row / column only come from real pixels (never the zero padding)
    assert (lvl1[-1, :][lvl1[-1, :] != -128] >= 10).all()
    assert (lvl1[:, -1][lvl1[:, -1] != -128] >= 10).all()


# --------------------------------------------------------------------------- antimeridian


def test_antimeridian_gap_uses_sentinel(tmp_path):
    lon = np.arange(170.5, 190.0, 1.0)
    lat = np.arange(9.5, 0.0, -1.0)
    data = np.ones((lat.size, lon.size), dtype="int8")
    ds = xr.Dataset({"cls": (("lat", "lon"), data)}, coords={"lon": lon, "lat": lat})
    ds = ds.rio.write_crs("EPSG:4326")
    out, _ = detect(ds)
    arr = out["cls"].values
    assert arr.dtype == np.int8
    assert (arr == -128).any(), "expected a sentinel-filled gap"
    assert set(np.unique(arr).tolist()) == {1, -128}
    store = _write(ds, tmp_path, 8)
    assert _disk_fill(store, "0", "cls") == -128


# --------------------------------------------------------------------------- preview / attrs


def test_preview_categorical_values(tmp_path):
    ds = _mask_ds(ny=64, nx=64)
    store = _write(ds, tmp_path, 16)
    cfg = pv.read_config(store)
    (v,) = [x for x in cfg["variables"] if x["name"] == "mask"]
    assert v["categorical"] is True
    assert v["values"] == [0.0, 1.0, 2.0]


def test_variables_attr_and_preview_order(multi_var, tmp_path):
    store = _write(multi_var, tmp_path, 16)
    attrs = dict(zarr.open_group(store, mode="r").attrs)
    order = attrs[VARIABLES_ATTR]
    assert order.index("melt") < order.index("mask")
    names = [v["name"] for v in pv.read_config(store)["variables"]]
    assert names.index("melt") < names.index("mask")
