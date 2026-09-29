"""Reference test on real data: OceanSODA-ETHZ-HR delta-fCO2 (D-15). Needs network."""

import warnings

import numpy as np
import pytest
import xarray as xr
import zarr

import geozarr_pyramid_maker as gpm

pytestmark = [pytest.mark.slow, pytest.mark.network]

URL = "https://s3.waw4-1.cloudferro.com/EarthCODE/OSCAssets/ocean-soda/dfco2.zarr/"
SHAPES = [(720, 1440), (360, 720), (180, 360)]


def _block_nanmean(a: np.ndarray) -> np.ndarray:
    *lead, ny, nx = a.shape
    blocks = a.reshape(*lead, ny // 2, 2, nx // 2, 2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks
        return np.nanmean(blocks, axis=(-3, -1)).astype("float32")


@pytest.fixture(scope="module")
def source() -> xr.Dataset:
    try:
        ds = xr.open_zarr(URL).isel(time=slice(0, 12))
        float(ds["dfco2"].isel(time=0, lat=0, lon=0))  # touch the data: fail early
    except Exception as exc:  # network down, TLS failure, ...
        pytest.skip(f"reference dataset unreachable: {exc!r}")
    return ds


@pytest.fixture(scope="module")
def written(source, tmp_path_factory):
    path = tmp_path_factory.mktemp("ref") / "ref.zarr"
    result = source.geozarr.to_pyramid(path)
    return path, result


def _level(path, k) -> xr.Dataset:
    return xr.open_zarr(path, group=str(k), consolidated=False, zarr_format=3)


def test_valid_and_structure(written, source):
    path, result = written
    assert gpm.is_valid(result.validation), result.validation
    assert gpm.is_valid(gpm.validate(path))
    assert len(result.plan.levels) == 3
    assert [lvl.shape for lvl in result.plan.levels] == SHAPES
    root = zarr.open_group(path, mode="r")
    for k, shape in enumerate(SHAPES):
        arr = root[f"{k}/dfco2"]
        assert arr.shape == (12, *shape)
        assert root[str(k)].attrs["proj:code"] == "EPSG:4326"
    assert tuple(root["0"].attrs["spatial:transform"]) == (0.25, 0, -180, 0, -0.25, 90)


def test_level0_equals_source_flipped(written, source):
    path, _ = written
    expected = source["dfco2"].values[:, ::-1, :]  # lat ascending -> north-up
    actual = _level(path, 0)["dfco2"].values
    np.testing.assert_array_equal(actual, expected)  # NaN-aware


def test_levels_are_block_means(written):
    path, _ = written
    lv = [_level(path, k)["dfco2"].values for k in range(3)]
    for k in (1, 2):
        np.testing.assert_allclose(lv[k], _block_nanmean(lv[k - 1]), rtol=1e-5, atol=1e-6)


def test_shards_match_plan(written):
    path, result = written
    root = zarr.open_group(path, mode="r")
    for k, level in enumerate(result.plan.levels):
        spec = level.chunks["dfco2"]
        arr = root[f"{k}/dfco2"]
        assert arr.shards == spec.shards
        assert arr.chunks == spec.chunks
