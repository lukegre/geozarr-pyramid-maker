import dask
import dask.array as da
import numpy as np
import pytest
import xarray as xr

from geozarr_pyramid_maker.detect import DetectionError, GridInfo, VarInfo, detect


def _val(da_):
    return da_.compute().item()


def _no_compute(*args, **kwargs):
    raise AssertionError("detect() must not compute dask data")


def _is_north_up(ds, info):
    y = ds[info.y_dim].values
    return bool(np.all(np.diff(y) < 0))


def _warnings(records):
    return [m for lvl, m in records if lvl == "WARNING"]


# ---------------------------------------------------------------- per-fixture basics


def test_global_0360(global_4326_0360, log_records):
    with dask.config.set(scheduler=_no_compute):
        ds, info = detect(global_4326_0360)
    assert (info.x_dim, info.y_dim) == ("lon", "lat")
    assert info.crs.to_epsg() == 4326
    assert info.shape == (180, 360)
    assert info.transform == pytest.approx((1.0, 0.0, -180.0, 0.0, -1.0, 90.0))
    assert info.bbox == pytest.approx((-180.0, -90.0, 180.0, 90.0))
    x = ds["lon"].values
    assert x.min() == -179.5
    assert x.max() == 179.5
    assert x.size == 360
    assert np.allclose(np.diff(x), 1.0, rtol=1e-9, atol=0)
    assert _is_north_up(ds, info)
    # values travelled with their coordinates (y flipped, lon rolled)
    assert _val(ds["sst"].sel(lat=-89.5, lon=0.5)) == 0
    assert _val(ds["sst"].sel(lat=10.5, lon=-0.5)) == 100 * 1000 + 359
    assert _val(ds["sst"].sel(lat=-89.5, lon=-179.5)) == 180
    assert ds["sst"].dtype == np.float32
    warns = " ".join(_warnings(log_records))
    assert "inferred" in warns.lower() or "assum" in warns.lower()
    assert any("flip" in w.lower() for w in _warnings(log_records))
    assert any("360" in w or "roll" in w.lower() for w in _warnings(log_records))


def test_regional_utm(regional_utm):
    ds, info = detect(regional_utm)
    assert (info.x_dim, info.y_dim) == ("x", "y")
    assert info.crs.to_epsg() == 32632
    assert info.shape == (1001, 777)
    a, b, c, d, e, f = info.transform
    assert (a, b, d, e) == (30.0, 0.0, 0.0, -30.0)
    assert c == 500_015.0 - 15.0
    assert f == 4_999_985.0 + 15.0
    assert info.bbox == (500_000.0, 5_000_000.0 - 30.0 * 1001, 500_000.0 + 30.0 * 777, 5_000_000.0)
    assert ds["dem"].dims == ("y", "x")
    assert ds["dem"].dtype == np.float32


def test_utm_fill_masked(regional_utm):
    ds, info = detect(regional_utm)
    v = ds["dem"]
    assert v.dtype == np.float32
    assert np.isnan(v.values[0, 0])
    assert np.isnan(v.values[500, 400])
    assert v.values[1, 1] == 1001
    assert "_FillValue" not in v.attrs
    assert "_FillValue" not in v.encoding
    (vi,) = info.variables
    assert np.isnan(vi.fill_value)
    assert vi.dtype == np.float32
    assert vi.shape == (1001, 777)
    assert vi.dims == ("y", "x")
    assert vi.categorical is False


def test_polar(polar_3031, log_records):
    with dask.config.set(scheduler=_no_compute):
        ds, info = detect(polar_3031)
    assert info.crs.to_epsg() == 3031
    assert info.shape == (300, 280)
    assert info.transform == (1000.0, 0.0, -140_000.0, 0.0, -1000.0, 150_000.0)
    assert info.bbox == (-140_000.0, -150_000.0, 140_000.0, 150_000.0)
    assert [v.name for v in info.variables] == ["melt"]
    assert "spatial_ref" not in ds.variables
    assert not _warnings(log_records)  # nothing was auto-corrected
    assert any(lvl == "DEBUG" and "spatial_ref" in m for lvl, m in log_records)
    assert sum(lvl == "INFO" for lvl, _ in log_records) == 1


def test_numpy_backed_stays_numpy(numpy_backed):
    ds, info = detect(numpy_backed)
    assert isinstance(ds["melt"].data, np.ndarray)
    assert info.shape == (300, 280)
    assert info.crs.to_epsg() == 3031


def test_dask_stays_dask(polar_3031, global_4326_0360, regional_antimeridian, multi_var):
    for src in (polar_3031, global_4326_0360, regional_antimeridian, multi_var):
        with dask.config.set(scheduler=_no_compute):
            ds, info = detect(src)
        for v in info.variables:
            assert isinstance(ds[v.name].data, da.Array), v.name


def test_multi_var(multi_var, log_records):
    with dask.config.set(scheduler=_no_compute):
        ds, info = detect(multi_var)
    assert info.crs.to_epsg() == 3031
    assert info.shape == (60, 50)
    assert set(ds.data_vars) == {"melt", "mask"}
    by_name = {v.name: v for v in info.variables}
    assert set(by_name) == {"melt", "mask"}
    assert by_name["mask"].categorical is True
    assert by_name["melt"].categorical is False
    assert by_name["mask"].dtype == np.int8
    assert by_name["melt"].dtype == np.float32
    assert by_name["mask"].fill_value == -1
    assert np.isnan(by_name["melt"].fill_value)
    assert by_name["melt"].dims == ("time", "y", "x")
    assert by_name["melt"].shape == (3, 60, 50)
    assert by_name["mask"].dims == ("y", "x")
    assert info.extra_dims == {"time": 3}
    # int fill is kept, not masked
    assert ds["mask"].values[0, 0] == -1
    dropped = " ".join(_warnings(log_records))
    assert "version" in dropped
    assert "time_series" in dropped
    assert "spatial_ref" not in dropped


def test_int_without_declared_fill_and_flags_categorical():
    ny, nx = 4, 5
    ds = xr.Dataset(
        {
            "cls": (("y", "x"), np.zeros((ny, nx), dtype="uint8")),
            "flagged": (
                ("y", "x"),
                np.zeros((ny, nx), dtype="float32"),
                {"flag_values": [0, 1], "flag_meanings": "a b"},
            ),
            "flt": (("y", "x"), np.zeros((ny, nx), dtype="float32")),
        },
        coords={"x": np.arange(nx) * 10.0, "y": -np.arange(ny) * 10.0},
    )
    _, info = detect(ds, crs="EPSG:32633")
    by = {v.name: v for v in info.variables}
    assert by["cls"].categorical and not by["cls"].fill_declared
    assert by["flagged"].categorical
    assert not by["flt"].categorical and np.isnan(by["flt"].fill_value)


def test_dims_transposed_spatial_last(polar_3031):
    ny, nx = 300, 280
    ds = polar_3031.assign(
        cube=(("x", "time", "y"), np.zeros((nx, 2, ny), dtype="float32")),
    ).transpose("x", ..., "y")
    out, info = detect(ds)
    by = {v.name: v for v in info.variables}
    assert by["cube"].dims == ("time", "y", "x")
    assert out["cube"].dims == ("time", "y", "x")
    assert by["cube"].shape == (2, 300, 280)
    assert info.extra_dims == {"time": 2}


def test_tiny(tiny):
    ds, info = detect(tiny)
    assert info.crs.to_epsg() == 4326
    assert info.shape == (100, 100)
    assert info.transform == pytest.approx((3.6, 0.0, -180.0, 0.0, -1.8, 90.0))
    assert info.bbox == pytest.approx((-180.0, -90.0, 180.0, 90.0))
    assert ds["lon"].values[0] == pytest.approx(-178.2)


def test_no_crs_projected_raises(no_crs_projected):
    with pytest.raises(DetectionError, match=r"crs="):
        detect(no_crs_projected)


def test_no_crs_projected_with_crs_arg(no_crs_projected):
    _, info = detect(no_crs_projected, crs=3031)
    assert info.crs.to_epsg() == 3031
    assert info.transform == (40_000.0, 0.0, -1_000_000.0, 0.0, -40_000.0, 1_000_000.0)


def test_curvilinear_raises(curvilinear):
    with pytest.raises(
        DetectionError, match=r"curvilinear or irregular grids are not supported in v0\.1"
    ):
        detect(curvilinear)


def test_irregular_1d_raises():
    x = np.array([0.0, 1.0, 2.0, 4.0, 8.0])
    ds = xr.Dataset({"v": (("y", "x"), np.zeros((3, 5)))}, coords={"x": x, "y": [2.0, 1.0, 0.0]})
    with pytest.raises(DetectionError, match=r"irregular grids are not supported in v0\.1"):
        detect(ds, crs=32633)


def test_no_spatial_dims_raises():
    ds = xr.Dataset({"v": (("foo", "bar"), np.zeros((3, 4)))})
    with pytest.raises(DetectionError, match=r"foo.*bar|bar.*foo") as ei:
        detect(ds)
    assert "rename" in str(ei.value)


def test_cf_axis_and_standard_name():
    ny, nx = 4, 6
    data = {"v": (("row", "col"), np.zeros((ny, nx), dtype="float32"))}
    by_axis = xr.Dataset(
        data,
        coords={
            "col": ("col", np.arange(nx) * 100.0, {"axis": "X"}),
            "row": ("row", -np.arange(ny) * 100.0, {"axis": "Y"}),
        },
    )
    _, info = detect(by_axis, crs=32633)
    assert (info.x_dim, info.y_dim) == ("col", "row")
    by_std = xr.Dataset(
        data,
        coords={
            "col": ("col", np.arange(nx) * 100.0, {"standard_name": "projection_x_coordinate"}),
            "row": ("row", -np.arange(ny) * 100.0, {"standard_name": "projection_y_coordinate"}),
        },
    )
    _, info = detect(by_std, crs=32633)
    assert (info.x_dim, info.y_dim) == ("col", "row")


def test_x_descending_raises():
    ds = xr.Dataset(
        {"v": (("y", "x"), np.zeros((3, 4)))},
        coords={"x": [30.0, 20.0, 10.0, 0.0], "y": [2.0, 1.0, 0.0]},
    )
    with pytest.raises(DetectionError, match="descending"):
        detect(ds, crs=32633)


def test_no_usable_variables_raises(polar_3031):
    ds = xr.Dataset({"s": ((), 1.0)}, coords={"x": polar_3031.x, "y": polar_3031.y})
    with pytest.raises(DetectionError, match="variable"):
        detect(ds, crs=3031)


def test_crs_override_wins(regional_utm):
    assert regional_utm.rio.crs.to_epsg() == 32632
    _, info = detect(regional_utm, crs="EPSG:32633")
    assert info.crs.to_epsg() == 32633


def test_dataarray_input(polar_3031):
    ds, info = detect(polar_3031["melt"])
    assert isinstance(ds, xr.Dataset)
    assert [v.name for v in info.variables] == ["melt"]
    unnamed = polar_3031["melt"].rename(None)
    ds, info = detect(unnamed)
    assert [v.name for v in info.variables] == ["data"]


def test_grid_mapping_attr_crs_without_rio():
    ny, nx = 3, 4
    ds = xr.Dataset(
        {"v": (("y", "x"), np.zeros((ny, nx), "float32"), {"grid_mapping": "crsvar"})},
        coords={"x": np.arange(nx) * 1000.0, "y": -np.arange(ny) * 1000.0},
    )
    ds["crsvar"] = xr.DataArray(
        0,
        attrs={
            "grid_mapping_name": "polar_stereographic",
            "latitude_of_projection_origin": -90.0,
            "standard_parallel": -71.0,
            "straight_vertical_longitude_from_pole": 0.0,
            "false_easting": 0.0,
            "false_northing": 0.0,
        },
    )
    out, info = detect(ds)
    assert info.crs.is_projected
    assert info.crs.to_epsg() in (3031, None)
    assert "crsvar" not in out.variables


# ---------------------------------------------------------------- orientation / longitude


def test_y_flip_moves_values():
    x = np.arange(4) * 10.0
    y = np.array([0.0, 10.0, 20.0])  # ascending
    data = np.arange(12, dtype="float32").reshape(3, 4)
    ds = xr.Dataset({"v": (("y", "x"), data)}, coords={"x": x, "y": y})
    out, info = detect(ds, crs=32633)
    assert _is_north_up(out, info)
    assert _val(out["v"].sel(y=0.0, x=10.0)) == 1
    assert _val(out["v"].sel(y=20.0, x=30.0)) == 11
    assert info.transform == (10.0, 0.0, -5.0, 0.0, -10.0, 25.0)


def test_lon_all_ge_180():
    lon = np.arange(200.5, 210.0, 1.0)
    lat = np.arange(9.5, -0.5, -1.0)
    data = (np.arange(lat.size)[:, None] * 100 + np.arange(lon.size)[None, :]).astype("float32")
    ds = xr.Dataset({"v": (("lat", "lon"), data)}, coords={"lon": lon, "lat": lat})
    out, info = detect(ds)
    assert out["lon"].values[0] == pytest.approx(-159.5)
    assert info.shape == (10, 10)
    assert info.transform[2] == pytest.approx(-160.0)
    assert _val(out["v"].sel(lat=9.5, lon=-159.5)) == 0
    assert _val(out["v"].sel(lat=0.5, lon=-150.5)) == 909


def test_antimeridian_gap(regional_antimeridian, log_records):
    with dask.config.set(scheduler=_no_compute):
        ds, info = detect(regional_antimeridian)
    assert info.crs.to_epsg() == 4326
    assert info.shape == (10, 360)
    assert info.transform == pytest.approx((1.0, 0.0, -180.0, 0.0, -1.0, 10.0))
    x = ds["lon"].values
    assert x[0] == pytest.approx(-179.5) and x[-1] == pytest.approx(179.5)
    assert np.allclose(np.diff(x), 1.0)
    v = ds["sst"]
    assert v.dtype == np.float32
    assert v.sel(lon=slice(-169.5, 169.5)).isnull().all()
    assert _val(v.sel(lon=179.5, lat=9.5)) == 9
    assert _val(v.sel(lon=170.5, lat=9.5)) == 0
    assert _val(v.sel(lon=-170.5, lat=9.5)) == 19  # lon 189.5, col 19
    assert _val(v.sel(lon=-179.5, lat=0.5)) == 9 * 1000 + 10  # lon 180.5 is col 10 of row 9
    assert any("antimeridian" in w.lower() for w in _warnings(log_records))


def test_antimeridian_int_gap_uses_fill():
    lon = np.arange(170.5, 190.0, 1.0)
    lat = np.arange(1.5, 0.0, -1.0)
    data = np.ones((lat.size, lon.size), dtype="int16")
    ds = xr.Dataset(
        {"m": (("lat", "lon"), data, {"_FillValue": -1})}, coords={"lon": lon, "lat": lat}
    ).chunk({"lon": 10})
    out, info = detect(ds)
    (vi,) = info.variables
    assert out["m"].dtype == np.int16
    assert vi.fill_value == -1
    assert out["m"].sel(lon=0.5).values.tolist() == [-1, -1]
    assert out["m"].sel(lon=170.5).values.tolist() == [1, 1]


def test_projected_large_x_not_rolled():
    x = 500_000.0 + np.arange(5) * 100.0
    ds = xr.Dataset({"v": (("y", "x"), np.zeros((2, 5)))}, coords={"x": x, "y": [1.0, 0.0]})
    out, _ = detect(ds, crs=32633)
    assert out["x"].values[0] == 500_000.0


def test_single_pixel_needs_res():
    ds = xr.Dataset({"v": (("y", "x"), np.zeros((1, 1)))}, coords={"x": [5.0], "y": [5.0]})
    with pytest.raises(DetectionError, match="res"):
        detect(ds, crs=32633)
    ds["x"].attrs["res"] = 10.0
    ds["y"].attrs["res"] = 10.0
    _, info = detect(ds, crs=32633)
    assert info.transform == (10.0, 0.0, 0.0, 0.0, -10.0, 10.0)


def test_gridinfo_types(polar_3031):
    _, info = detect(polar_3031)
    assert isinstance(info, GridInfo)
    assert all(isinstance(v, VarInfo) for v in info.variables)
    assert isinstance(info.variables, tuple)
    with pytest.raises(AttributeError):
        info.shape = (1, 1)  # frozen


# ------------------------------------------------------------- float32 coordinate noise


def _f32_ds(x, y=None):
    if y is None:
        y = np.array([1.0, 0.0])
    return xr.Dataset(
        {"v": (("y", "x"), np.zeros((y.size, x.size), dtype="float32"))},
        coords={"x": x, "y": y},
    )


@pytest.mark.parametrize(
    "x",
    [
        np.linspace(-179.95, 179.95, 3600).astype("float32"),
        (np.arange(6435) * 1000.3 + 123456.7).astype("float32"),
    ],
)
def test_float32_coords_accepted(x):
    _, info = detect(_f32_ds(x), crs="EPSG:3857")
    assert info.shape[1] == x.size


def test_float32_global_01_transform():
    x = (np.arange(3600) * 0.1 - 179.95).astype("float32")
    y = (89.95 - np.arange(1800) * 0.1).astype("float32")
    _, info = detect(_f32_ds(x, y), crs="EPSG:4326")
    a, _, c, _, e, f = info.transform
    assert abs(a - 0.1) < 1e-9
    assert abs(e + 0.1) < 1e-9
    assert abs(c + 180.0) < 1e-4
    assert abs(f - 90.0) < 1e-4


def test_float32_irregular_raises():
    x = (np.arange(3600) * 0.1 - 179.95).astype("float64")
    x[1800:] += 0.001  # 1% step change
    with pytest.raises(DetectionError, match="regular"):
        detect(_f32_ds(x.astype("float32")), crs="EPSG:4326")
