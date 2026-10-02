import numpy as np
import pytest
import xarray as xr
from loguru import logger
from typer.testing import CliRunner

import geozarr_pyramid_maker as gpm
from geozarr_pyramid_maker.cli import _open_input, _parse_resampling, app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _restore_loguru():
    yield
    logger.remove()
    import sys

    logger.add(sys.stderr, level="DEBUG")


@pytest.fixture
def nc_in(multi_var, tmp_path):
    p = tmp_path / "in.nc"
    multi_var.compute().to_netcdf(p)
    return p


@pytest.fixture
def zarr_in(multi_var, tmp_path):
    p = tmp_path / "in.zarr"
    multi_var.to_zarr(p, zarr_format=3, consolidated=False)
    return p


def _run(*args):
    return runner.invoke(app, [str(a) for a in args])


def test_help_lists_commands():
    r = _run("--help")
    assert r.exit_code == 0
    for c in ("convert", "plan", "validate", "version"):
        assert c in r.output


def test_convert_netcdf_validates(nc_in, tmp_path):
    out = tmp_path / "out.zarr"
    r = _run("convert", nc_in, out, "--tile-size", 32)
    assert r.exit_code == 0, r.output + r.stderr
    assert "levels:" in r.stdout and "validation: ok" in r.stdout
    assert gpm.is_valid(gpm.validate(out))
    assert _run("validate", out).exit_code == 0


def test_convert_zarr_input(zarr_in, tmp_path):
    out = tmp_path / "out.zarr"
    r = _run("convert", zarr_in, out, "--tile-size", 32)
    assert r.exit_code == 0, r.stderr
    assert gpm.is_valid(gpm.validate(out))


def test_var_subset(nc_in, tmp_path):
    out = tmp_path / "out.zarr"
    r = _run("convert", nc_in, out, "--tile-size", 32, "--var", "melt")
    assert r.exit_code == 0, r.stderr
    ds = xr.open_zarr(out, group="0", consolidated=False)
    assert "melt" in ds and "mask" not in ds


def test_var_unknown(nc_in, tmp_path):
    r = _run("convert", nc_in, tmp_path / "o.zarr", "--var", "nope")
    assert r.exit_code == 2
    assert "nope" in r.stderr and "Traceback" not in r.stderr


def test_resampling_single_and_pairs(nc_in, tmp_path):
    r = _run("convert", nc_in, tmp_path / "a.zarr", "--tile-size", 32, "--resampling", "nearest")
    assert r.exit_code == 0, r.stderr
    r = _run(
        "convert", nc_in, tmp_path / "b.zarr", "--tile-size", 32,
        "--resampling", "melt=max", "--resampling", "mask=mode",
    )  # fmt: skip
    assert r.exit_code == 0, r.stderr


def test_resampling_mixed_is_error(nc_in, tmp_path):
    r = _run("convert", nc_in, tmp_path / "o.zarr", "--resampling", "mean", "--resampling", "a=max")
    assert r.exit_code == 2
    assert "either" in r.stderr


def test_parse_resampling():
    assert _parse_resampling(None) == "auto"
    assert _parse_resampling(["mean"]) == "mean"
    assert _parse_resampling(["a=max", "b=min"]) == {"a": "max", "b": "min"}


def test_overwrite(nc_in, tmp_path):
    out = tmp_path / "out.zarr"
    assert _run("convert", nc_in, out, "--tile-size", 32).exit_code == 0
    r = _run("convert", nc_in, out, "--tile-size", 32)
    assert r.exit_code == 2
    assert "overwrite" in r.stderr and "Traceback" not in r.stderr
    r = _run("convert", nc_in, out, "--tile-size", 32, "--overwrite")
    assert r.exit_code == 0, r.stderr
    assert gpm.is_valid(gpm.validate(out))


def test_no_validate(nc_in, tmp_path):
    r = _run("convert", nc_in, tmp_path / "o.zarr", "--tile-size", 32, "--no-validate")
    assert r.exit_code == 0
    assert "validation: skipped" in r.stdout


def test_plan_output(nc_in):
    r = _run("plan", nc_in, "--tile-size", 32)
    assert r.exit_code == 0, r.stderr
    assert "melt" in r.stdout or "mask" in r.stdout
    assert len(r.stdout.strip().splitlines()) >= 3


def test_validate_exit_codes(nc_in, tmp_path):
    out = tmp_path / "out.zarr"
    _run("convert", nc_in, out, "--tile-size", 32)
    r = _run("validate", out)
    assert r.exit_code == 0
    assert "multiscales: ok" in r.stdout
    assert _run("validate", tmp_path / "missing.zarr").exit_code == 2
    bad = tmp_path / "bad.zarr"
    xr.Dataset({"a": ("x", np.arange(3.0))}).to_zarr(bad, zarr_format=3)
    r = _run("validate", bad)
    assert r.exit_code == 1
    assert (
        "multiscales:" in r.stdout and "ok" not in r.stdout.split("multiscales:")[1].split("\n")[0]
    )


def test_quiet_and_verbose(nc_in, tmp_path):
    r = _run("-q", "convert", nc_in, tmp_path / "q.zarr", "--tile-size", 32)
    assert r.exit_code == 0
    assert "Level 0 written" not in r.stderr
    r = _run("convert", nc_in, tmp_path / "i.zarr", "--tile-size", 32)
    assert "Level 0 written" in r.stderr and "esampling for" not in r.stderr
    r = _run("-v", "convert", nc_in, tmp_path / "v.zarr", "--tile-size", 32)
    assert "esampling for" in r.stderr


def test_geotiff_input(regional_utm, tmp_path):
    tif = tmp_path / "dem.tif"
    regional_utm["dem"].rio.to_raster(tif)
    ds = _open_input(str(tif))
    assert "band" not in ds.dims
    out = tmp_path / "out.zarr"
    r = _run("convert", tif, out)
    assert r.exit_code == 0, r.stderr
    assert gpm.is_valid(gpm.validate(out))


def test_preview_demo_serves_demo_store(monkeypatch):
    from importlib import import_module

    pv = import_module("geozarr_pyramid_maker.preview")

    seen = {}
    monkeypatch.setattr(pv, "serve_viewer", lambda root, **kw: seen.update(root=root, **kw))
    r = runner.invoke(app, ["preview", "--demo", "--port", "8765"])
    assert r.exit_code == 0, r.output
    assert seen["start"] == (pv.DEMO_STORE, pv.DEMO_ENDPOINT)
    assert seen["port"] == 8765 and seen["root"] == "."


@pytest.mark.parametrize(
    "extra", [["s3://b/a.zarr"], ["--endpoint", "https://x"], ["--serve"], ["--out", "o.html"]]
)
def test_preview_demo_conflicts(monkeypatch, extra):
    from importlib import import_module

    pv = import_module("geozarr_pyramid_maker.preview")

    monkeypatch.setattr(pv, "serve_viewer", lambda *a, **k: pytest.fail("must not serve"))
    r = runner.invoke(app, ["preview", "--demo", *extra])
    assert r.exit_code == 2
