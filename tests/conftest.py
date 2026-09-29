"""Synthetic dataset fixtures (PLAN §6). Small, NumPy-generated, fast.

Pixel values encode their (row, col) index as ``row * 1000 + col`` (in the *original*
orientation) so tests can check that values travelled with their coordinates.
"""

import numpy as np
import pytest
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from loguru import logger


def _index_grid(ny: int, nx: int) -> np.ndarray:
    return (np.arange(ny)[:, None] * 1000 + np.arange(nx)[None, :]).astype("float32")


@pytest.fixture
def log_records():
    """Capture loguru output as a list of ``(level_name, message)`` tuples."""
    records: list[tuple[str, str]] = []
    handler_id = logger.add(
        lambda m: records.append((m.record["level"].name, m.record["message"])),
        level="TRACE",
        format="{message}",
    )
    yield records
    logger.remove(handler_id)


@pytest.fixture
def global_4326_0360():
    """Global 1 degree grid, lon 0.5..359.5, lat ascending, no CRS info, dask-backed."""
    lon = np.arange(0.5, 360.0, 1.0)
    lat = np.arange(-89.5, 90.0, 1.0)
    ds = xr.Dataset(
        {"sst": (("lat", "lon"), _index_grid(lat.size, lon.size), {"units": "degC"})},
        coords={"lon": lon, "lat": lat},
    )
    return ds.chunk({"lat": 90, "lon": 90})


@pytest.fixture
def regional_utm():
    """EPSG:32632, 1001 x 777 at 30 m, y descending, ``_FillValue=-9999`` in some pixels."""
    ny, nx = 1001, 777
    x = 500_015.0 + 30.0 * np.arange(nx)
    y = 4_999_985.0 - 30.0 * np.arange(ny)
    data = _index_grid(ny, nx)
    data[0, 0] = -9999
    data[500, 400] = -9999
    ds = xr.Dataset(
        {"dem": (("y", "x"), data, {"_FillValue": -9999.0, "units": "m"})},
        coords={"x": x, "y": y},
    )
    return ds.rio.write_crs("EPSG:32632")


def _polar(chunked: bool) -> xr.Dataset:
    ny, nx = 300, 280
    x = -140_000.0 + 500.0 + 1000.0 * np.arange(nx)
    y = 150_000.0 - 500.0 - 1000.0 * np.arange(ny)
    ds = xr.Dataset(
        {"melt": (("y", "x"), _index_grid(ny, nx), {"units": "m/yr", "long_name": "melt"})},
        coords={"x": x, "y": y},
    )
    ds = ds.rio.write_crs("EPSG:3031").rio.write_grid_mapping("spatial_ref")
    return ds.chunk({"y": 100, "x": 100}) if chunked else ds


@pytest.fixture
def polar_3031():
    return _polar(chunked=True)


@pytest.fixture
def numpy_backed():
    return _polar(chunked=False)


@pytest.fixture
def multi_var():
    """EPSG:3031: float melt(time,y,x), int8 mask(y,x) with flags, scalar and 1-D vars."""
    ny, nx, nt = 60, 50, 3
    x = -25_000.0 + 500.0 + 1000.0 * np.arange(nx)
    y = 30_000.0 - 500.0 - 1000.0 * np.arange(ny)
    time = np.array(["2020-01-01", "2020-02-01", "2020-03-01"], dtype="datetime64[ns]")
    melt = np.stack([_index_grid(ny, nx) + 100_000 * t for t in range(nt)])
    mask = (np.arange(ny * nx).reshape(ny, nx) % 3).astype("int8")
    mask[0, 0] = -1
    ds = xr.Dataset(
        {
            "melt": (("time", "y", "x"), melt, {"units": "m/yr"}),
            "mask": (
                ("y", "x"),
                mask,
                {
                    "flag_values": [0, 1, 2],
                    "flag_meanings": "ocean grounded floating",
                    "_FillValue": -1,
                },
            ),
            "version": ((), 3),
            "time_series": (("time",), np.arange(nt, dtype="float64")),
        },
        coords={"x": x, "y": y, "time": time},
    )
    ds = ds.rio.write_crs("EPSG:3031")
    return ds.chunk({"y": 30, "x": 25})


@pytest.fixture
def tiny():
    """100 x 100 global EPSG:4326 in -180..180 with a spatial_ref variable."""
    n = 100
    lon = -180.0 + 1.8 + 3.6 * np.arange(n)
    lat = 90.0 - 0.9 - 1.8 * np.arange(n)
    ds = xr.Dataset({"t": (("lat", "lon"), _index_grid(n, n))}, coords={"lon": lon, "lat": lat})
    return ds.rio.write_crs("EPSG:4326")


@pytest.fixture
def no_crs_projected():
    n = 50
    x = -1_000_000.0 + 20_000.0 + 40_000.0 * np.arange(n)
    y = 1_000_000.0 - 20_000.0 - 40_000.0 * np.arange(n)
    return xr.Dataset({"v": (("y", "x"), _index_grid(n, n))}, coords={"x": x, "y": y})


@pytest.fixture
def curvilinear():
    nj, ni = 20, 30
    jj, ii = np.meshgrid(np.arange(nj), np.arange(ni), indexing="ij")
    lat = 40.0 + 0.1 * jj + 0.01 * ii
    lon = 10.0 + 0.1 * ii - 0.01 * jj
    return xr.Dataset(
        {"v": (("nj", "ni"), _index_grid(nj, ni))},
        coords={"lat": (("nj", "ni"), lat), "lon": (("nj", "ni"), lon)},
    )


@pytest.fixture
def regional_antimeridian():
    """EPSG:4326, lon 170.5..189.5 (crosses 180), lat descending, dask-backed."""
    lon = np.arange(170.5, 190.0, 1.0)
    lat = np.arange(9.5, 0.0, -1.0)
    ds = xr.Dataset(
        {"sst": (("lat", "lon"), _index_grid(lat.size, lon.size))},
        coords={"lon": lon, "lat": lat},
    )
    return ds.chunk({"lat": 5, "lon": 10})
