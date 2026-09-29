"""Tests for plan.py and the .geozarr.plan() accessor (PLAN 4.2)."""

# ruff: noqa: RUF001

import dask
import numpy as np
import pyproj
import pytest
import xarray as xr

import geozarr_pyramid_maker as gpm
from geozarr_pyramid_maker import DetectionError, PyramidPlan, build_plan, detect
from geozarr_pyramid_maker.detect import GridInfo, VarInfo
from geozarr_pyramid_maker.plan import RESAMPLING_METHODS, resolve_resampling


def _no_compute(*args, **kwargs):
    raise AssertionError("plan() must not compute dask data")


def _var(name="v", dtype="float32", extra=(), shape=(10, 10), categorical=False):
    extra_shape = tuple(extra.values()) if isinstance(extra, dict) else tuple(extra)
    return VarInfo(
        name=name,
        dtype=np.dtype(dtype),
        dims=(*(f"e{i}" for i in range(len(extra_shape))), "y", "x"),
        shape=(*extra_shape, *shape),
        categorical=categorical,
        fill_value=None,
    )


def _grid(ny, nx, variables=None, extra_dims=None, transform=(10.0, 0.0, 100.0, 0.0, -10.0, 500.0)):
    variables = variables or (_var(shape=(ny, nx)),)
    return GridInfo(
        x_dim="x",
        y_dim="y",
        crs=pyproj.CRS.from_epsg(32632),
        shape=(ny, nx),
        transform=transform,
        variables=tuple(variables),
        extra_dims=extra_dims or {},
    )


def _plan(ds, **kw):
    return build_plan(detect(ds)[1], **kw)


# ------------------------------------------------------------------ fixtures: levels

# name -> (shapes per level, transform at level 0, bbox at each level)
CASES = {
    "polar_3031": (
        [(300, 280)],
        (1000.0, 0.0, -140_000.0, 0.0, -1000.0, 150_000.0),
        [(-140_000.0, -150_000.0, 140_000.0, 150_000.0)],
    ),
    "numpy_backed": (
        [(300, 280)],
        (1000.0, 0.0, -140_000.0, 0.0, -1000.0, 150_000.0),
        [(-140_000.0, -150_000.0, 140_000.0, 150_000.0)],
    ),
    "regional_utm": (
        [(1001, 777), (501, 389)],
        (30.0, 0.0, 500_000.0, 0.0, -30.0, 5_000_000.0),
        [
            (500_000.0, 4_969_970.0, 523_310.0, 5_000_000.0),
            (500_000.0, 4_969_940.0, 523_340.0, 5_000_000.0),
        ],
    ),
    "global_4326_0360": (
        [(180, 360)],
        (1.0, 0.0, -180.0, 0.0, -1.0, 90.0),
        [(-180.0, -90.0, 180.0, 90.0)],
    ),
    "tiny": (
        [(100, 100)],
        (3.6, 0.0, -180.0, 0.0, -1.8, 90.0),
        [(-180.0, -90.0, 180.0, 90.0)],
    ),
    "multi_var": (
        [(60, 50)],
        (1000.0, 0.0, -25_000.0, 0.0, -1000.0, 30_000.0),
        [(-25_000.0, -30_000.0, 25_000.0, 30_000.0)],
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_levels_transforms_bbox(name, request):
    shapes, t0, bboxes = CASES[name]
    plan = _plan(request.getfixturevalue(name))
    assert isinstance(plan, PyramidPlan)
    assert len(plan.levels) == len(shapes)
    for k, level in enumerate(plan.levels):
        assert level.index == k
        assert level.shape == shapes[k]
        a, _, c, _, e, f = t0
        assert level.transform == (a * 2**k, 0.0, c, 0.0, e * 2**k, f)
        assert level.bbox == pytest.approx(bboxes[k])


def test_antimeridian_levels(regional_antimeridian):
    plan = _plan(regional_antimeridian)
    assert len(plan.levels) == 1
    assert plan.levels[0].shape == (10, 360)
    assert plan.levels[0].transform == pytest.approx((1.0, 0.0, -180.0, 0.0, -1.0, 10.0))


def test_worked_example_utm(regional_utm):
    plan = _plan(regional_utm)
    assert [lv.shape for lv in plan.levels] == [(1001, 777), (501, 389)]
    assert plan.tile_size == 512
    assert plan.shard_size == 128 * 1024**2


# ------------------------------------------------------------------ resampling


def test_resampling_auto_multi_var(multi_var):
    plan = _plan(multi_var)
    assert plan.resampling == {"melt": "mean", "mask": "mode"}


def test_resampling_single_method(multi_var):
    plan = _plan(multi_var, resampling="nearest")
    assert plan.resampling == {"melt": "nearest", "mask": "nearest"}


def test_resampling_dict_fallback_and_auto_value(multi_var):
    plan = _plan(multi_var, resampling={"melt": "max"})
    assert plan.resampling == {"melt": "max", "mask": "mode"}
    plan = _plan(multi_var, resampling={"melt": "auto", "mask": "nearest"})
    assert plan.resampling == {"melt": "mean", "mask": "nearest"}


def test_resampling_unknown_method(multi_var):
    grid = detect(multi_var)[1]
    with pytest.raises(ValueError, match="bilinear"):
        resolve_resampling(grid, "bilinear")
    with pytest.raises(ValueError, match="cubic"):
        resolve_resampling(grid, {"melt": "cubic"})


def test_resampling_unknown_variable_lists_valid(multi_var):
    grid = detect(multi_var)[1]
    with pytest.raises(ValueError, match="nope") as exc:
        resolve_resampling(grid, {"nope": "mean"})
    assert "melt" in str(exc.value) and "mask" in str(exc.value)


def test_resampling_methods_constant():
    assert RESAMPLING_METHODS == ("mean", "nearest", "mode", "min", "max")


def test_resampling_warns_on_categorical_mean(multi_var, log_records):
    _plan(multi_var, resampling={"mask": "mean"})
    warnings = [m for lvl, m in log_records if lvl == "WARNING" and "resampling" in m]
    assert len(warnings) == 1 and "mask" in warnings[0]


def test_resampling_no_warning_on_auto_or_float(multi_var, log_records):
    _plan(multi_var)
    _plan(multi_var, resampling={"melt": "max"})
    assert not [m for lvl, m in log_records if lvl == "WARNING" and "resampling" in m]


def test_resampling_debug_log(multi_var, log_records):
    _plan(multi_var)
    debug = [m for lvl, m in log_records if lvl == "DEBUG"]
    assert any("melt" in m and "mean" in m for m in debug)
    assert any("mask" in m and "mode" in m for m in debug)


# ------------------------------------------------------------------ chunks


def test_tiny_single_level_no_shard(tiny):
    plan = _plan(tiny)
    spec = plan.levels[0].chunks["t"]
    assert spec.chunks == (100, 100)
    assert spec.shards is None


def test_multi_var_chunks(multi_var):
    lv = _plan(multi_var).levels[0]
    assert lv.chunks["melt"].chunks == (1, 60, 50)
    assert lv.chunks["melt"].shards is None
    assert lv.chunks["mask"].chunks == (60, 50)
    assert lv.var_shape("melt") == (3, 60, 50)
    assert lv.var_shape("mask") == (60, 50)


def test_utm_chunks(regional_utm):
    l0, l1 = _plan(regional_utm).levels
    assert l0.chunks["dem"].chunks == (512, 512)
    assert l0.chunks["dem"].shards is None  # 2x2 = 4 chunks: sharding adds nothing
    assert l1.chunks["dem"].chunks == (501, 389)
    assert l1.chunks["dem"].shards is None


def test_big_grid_levels_and_shards():
    plan = build_plan(_grid(10000, 8000))
    assert [lv.shape for lv in plan.levels] == [
        (10000, 8000),
        (5000, 4000),
        (2500, 2000),
        (1250, 1000),
        (625, 500),
        (313, 250),
    ]
    l0 = plan.levels[0]
    assert l0.chunks["v"].chunks == (512, 512)
    assert l0.chunks["v"].shards == (4096, 4096)
    assert l0.nbytes == 10000 * 8000 * 4
    assert plan.levels[1].nbytes == 5000 * 4000 * 4
    assert plan.levels[-1].chunks["v"].shards is None
    for k, lv in enumerate(plan.levels):
        assert lv.transform == (10.0 * 2**k, 0.0, 100.0, 0.0, -10.0 * 2**k, 500.0)
    assert plan.levels[3].bbox == (100.0, 500.0 - 80 * 1250, 100.0 + 80 * 1000, 500.0)


def test_time_extension_on_coarse_level():
    var = _var("v", extra={"time": 5}, shape=(10000, 8000))
    plan = build_plan(_grid(10000, 8000, variables=[var], extra_dims={"time": 5}))
    assert plan.levels[0].chunks["v"].chunks == (1, 512, 512)
    assert plan.levels[0].chunks["v"].shards == (1, 4096, 4096)
    # level 3 is 1250x1000: the shard covers it, so it extends along time
    assert plan.levels[3].chunks["v"].shards == (5, 1536, 1024)
    assert plan.levels[3].var_shape("v") == (5, 1250, 1000)
    assert plan.levels[3].nbytes == 5 * 1250 * 1000 * 4


def test_nbytes_sums_variables(multi_var):
    lv = _plan(multi_var).levels[0]
    assert lv.nbytes == 3 * 60 * 50 * 4 + 60 * 50 * 1


def test_max_levels():
    grid = _grid(10000, 8000)
    assert len(build_plan(grid, max_levels=1).levels) == 1
    assert len(build_plan(grid, max_levels=3).levels) == 3
    assert len(build_plan(grid, max_levels=50).levels) == 6
    assert build_plan(grid, max_levels=1).levels[0].shape == (10000, 8000)


def test_tile_size_and_shard_size_passthrough():
    plan = build_plan(_grid(1000, 1000), tile_size=256, shard_size="8MiB")
    assert plan.tile_size == 256
    assert plan.shard_size == 8 * 1024**2
    assert [lv.shape for lv in plan.levels] == [(1000, 1000), (500, 500), (250, 250)]
    assert plan.levels[0].chunks["v"].chunks == (256, 256)


def test_validation_errors():
    grid = _grid(100, 100)
    with pytest.raises(ValueError):
        build_plan(grid, tile_size=0)
    with pytest.raises(ValueError):
        build_plan(grid, max_levels=0)
    with pytest.raises(ValueError):
        build_plan(grid, shard_size="junk")


# ------------------------------------------------------------------ repr


def test_repr_polar(polar_3031):
    text = repr(_plan(polar_3031))
    assert text == str(_plan(polar_3031))
    assert "EPSG:3031" in text
    assert "melt" in text and "mean" in text and "float32" in text
    assert "300×280" in text


def test_repr_levels_and_sizes():
    text = repr(build_plan(_grid(10000, 8000)))
    for shape in ("10000×8000", "5000×4000", "313×250"):
        assert shape in text
    assert "512²" in text and "4096²" in text
    assert "MiB" in text or "GiB" in text
    assert "10" in text  # resolution


def test_repr_no_authority_uses_name():
    grid = _grid(10, 10)
    wkt_only = pyproj.CRS.from_wkt(
        'PROJCS["Custom",'
        'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
        'PROJECTION["Transverse_Mercator"],'
        'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",9],'
        'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",0],'
        'PARAMETER["false_northing",0],UNIT["metre",1]]'
    )
    object.__setattr__(grid, "crs", wkt_only)
    assert "Custom" in repr(build_plan(grid))


# ------------------------------------------------------------------ accessor


def test_accessor_dataset(polar_3031):
    plan = polar_3031.geozarr.plan()
    assert isinstance(plan, PyramidPlan)
    assert plan.levels[0].shape == (300, 280)
    assert plan.grid.crs.to_epsg() == 3031


def test_accessor_dataarray(regional_utm):
    plan = regional_utm["dem"].geozarr.plan(max_levels=1, tile_size=256)
    assert len(plan.levels) == 1
    assert plan.tile_size == 256
    assert list(plan.resampling) == ["dem"]


def test_accessor_crs_override(no_crs_projected):
    plan = no_crs_projected.geozarr.plan(crs="EPSG:3035")
    assert plan.grid.crs.to_epsg() == 3035


@pytest.mark.parametrize("name", ["curvilinear", "no_crs_projected"])
def test_accessor_detection_errors(name, request):
    with pytest.raises(DetectionError):
        request.getfixturevalue(name).geozarr.plan()


def test_accessor_logs_table_at_debug(polar_3031, log_records):
    polar_3031.geozarr.plan()
    assert any(lvl == "DEBUG" and "300×280" in m for lvl, m in log_records)


def test_accessor_no_compute(polar_3031, multi_var, global_4326_0360):
    for src in (polar_3031, multi_var, global_4326_0360, multi_var["melt"]):
        with dask.config.set(scheduler=_no_compute):
            src.geozarr.plan()


def test_to_pyramid_delegates_to_write(polar_3031, monkeypatch):
    calls = {}

    def fake(obj, store, **kwargs):
        calls.update(obj=obj, store=store, kwargs=kwargs)
        return "result"

    monkeypatch.setattr("geozarr_pyramid_maker.accessor._to_pyramid", fake)
    assert polar_3031.geozarr.to_pyramid("somewhere.zarr", tile_size=64) == "result"
    assert calls["obj"] is polar_3031
    assert calls["store"] == "somewhere.zarr"
    assert calls["kwargs"] == {"tile_size": 64}


def test_public_api():
    for name in (
        "PyramidPlan",
        "LevelPlan",
        "build_plan",
        "detect",
        "DetectionError",
        "configure_logging",
        "__version__",
    ):
        assert name in gpm.__all__
        assert hasattr(gpm, name)
    assert hasattr(xr.Dataset, "geozarr") and hasattr(xr.DataArray, "geozarr")
