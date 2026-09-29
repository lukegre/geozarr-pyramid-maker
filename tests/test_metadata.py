"""Tests for metadata.py (M3): GeoZarr attrs, conventions and CF grid mapping."""

import json

import pyproj
import pytest
import rasterio
import rioxarray  # noqa: F401
import xarray as xr
import zarr
from geozarr_toolkit import validate_group
from zarr.storage import LocalStore

from geozarr_pyramid_maker import metadata as md
from geozarr_pyramid_maker.detect import detect
from geozarr_pyramid_maker.plan import build_plan
from geozarr_pyramid_maker.write import to_pyramid

FIXTURES = [
    "global_4326_0360",
    "regional_utm",
    "polar_3031",
    "multi_var",
    "tiny",
    "regional_antimeridian",
]
UUIDS = {
    "d35379db-88df-4056-af3a-620245f8e347",
    "689b58e2-cf7b-45e0-9fff-9cfc0883d6b4",
    "f17cb550-5864-4468-aeb7-f3180cfb622f",
}


def _tile(name):
    return 64 if name in ("regional_utm", "global_4326_0360") else 16


def _write(request, name, tmp_path, **kw):
    ds = request.getfixturevalue(name)
    path = tmp_path / "o.zarr"
    res = to_pyramid(ds, path, tile_size=_tile(name), validate=False, **kw)
    return path, res, ds


def _root(path):
    return zarr.open_group(LocalStore(path), mode="r")


@pytest.mark.parametrize("name", FIXTURES)
def test_toolkit_validate_group(name, request, tmp_path):
    path, res, _ = _write(request, name, tmp_path)
    root = _root(path)
    assert all(not e for e in validate_group(root).values())
    for k in range(len(res.plan.levels)):
        rep = validate_group(root[str(k)])
        assert set(rep) >= {"spatial", "proj"}
        assert all(not e for e in rep.values()), rep


def test_conventions_uuids_and_urls(polar_3031, tmp_path):
    path, _, _ = _write_simple(polar_3031, tmp_path)
    conv = _root(path).attrs["zarr_conventions"]
    assert {c["uuid"] for c in conv} == UUIDS
    urls = {c["name"]: c["schema_url"] for c in conv}
    assert urls["multiscales"].endswith("zarr-conventions/multiscales/refs/tags/v0.1/schema.json")
    assert urls["spatial:"].endswith("zarr-conventions/spatial/refs/tags/v0.1/schema.json")
    assert urls["proj:"].endswith("zarr-conventions/proj/refs/tags/v0.1/schema.json")
    specs = {c["name"]: c["spec_url"] for c in conv}
    for name, repo in (("multiscales", "multiscales"), ("spatial:", "spatial"), ("proj:", "proj")):
        assert specs[name] == f"https://github.com/zarr-conventions/{repo}/blob/v0.1/README.md"
    lvl = _root(path)["1"].attrs["zarr_conventions"]
    assert {c["name"] for c in lvl} == {"spatial:", "proj:"}


def _write_simple(ds, tmp_path, **kw):
    path = tmp_path / "o.zarr"
    res = to_pyramid(ds, path, tile_size=kw.pop("tile_size", 16), validate=False, **kw)
    return path, res, ds


def test_layout_entries(polar_3031, tmp_path):
    path, res, _ = _write_simple(polar_3031, tmp_path)
    layout = _root(path).attrs["multiscales"]["layout"]
    assert len(layout) == len(res.plan.levels) >= 3
    assert layout[0]["asset"] == "0" and "derived_from" not in layout[0]
    for k, entry in enumerate(layout):
        assert entry["asset"] == str(k)
        assert entry["spatial:transform"] == list(res.plan.levels[k].transform)
        assert entry["spatial:shape"] == list(res.plan.levels[k].shape)
        if k:
            assert entry["derived_from"] == str(k - 1)
            assert entry["transform"] == {"scale": [2.0, 2.0], "translation": [0.0, 0.0]}
    attrs = _root(path).attrs
    assert attrs["spatial:bbox"] == list(res.plan.levels[0].bbox)
    assert attrs["spatial:dimensions"] == ["y", "x"]
    assert "spatial:transform" not in attrs


def test_resampling_method_uniform(polar_3031, tmp_path):
    path, _, _ = _write_simple(polar_3031, tmp_path)
    assert _root(path).attrs["multiscales"]["resampling_method"] == "average"
    assert _root(path)["0"]["melt"].attrs["resampling_method"] == "average"


def test_resampling_method_mixed(multi_var, tmp_path):
    path, _, _ = _write_simple(multi_var, tmp_path)
    root = _root(path)
    assert "resampling_method" not in root.attrs["multiscales"]
    for k in ("0", "1"):
        assert root[k]["melt"].attrs["resampling_method"] == "average"
        assert root[k]["mask"].attrs["resampling_method"] == "mode"


@pytest.mark.parametrize("name", ["polar_3031", "regional_utm", "tiny"])
def test_proj_code_and_wkt2(name, request, tmp_path):
    path, res, _ = _write_simple(request.getfixturevalue(name), tmp_path, tile_size=64)
    for attrs in (_root(path).attrs, _root(path)["0"].attrs):
        assert attrs["proj:code"] == f"EPSG:{res.plan.grid.crs.to_epsg()}"
        assert "wkt2" not in attrs["proj:code"].lower()
        assert pyproj.CRS.from_wkt(attrs["proj:wkt2"]) == res.plan.grid.crs


def test_custom_crs_wkt2_only(no_crs_projected, tmp_path):
    crs = pyproj.CRS.from_proj4("+proj=stere +lat_0=-90 +lat_ts=-61.3 +lon_0=17 +datum=WGS84")
    assert crs.to_authority() is None
    path, _, _ = _write_simple(no_crs_projected, tmp_path, crs=crs, tile_size=16)
    attrs = _root(path).attrs
    assert "proj:code" not in attrs
    assert pyproj.CRS.from_wkt(attrs["proj:wkt2"]).equals(crs, ignore_axis_order=True)
    assert all(not e for e in validate_group(_root(path)).values())


def test_create_proj_attrs_accepts_both():
    crs = pyproj.CRS.from_epsg(3031)
    a = md.proj_attrs(crs)
    assert set(a) == {"proj:code", "proj:wkt2"}


def test_history_and_source_attrs(polar_3031, tmp_path):
    ds = polar_3031.copy()
    ds.attrs = {"title": "Melt", "history": "2020-01-01: made", "spatial:foo": 1}
    path, _, _ = _write_simple(ds, tmp_path)
    attrs = _root(path).attrs
    assert attrs["title"] == "Melt"
    assert "spatial:foo" not in attrs
    lines = attrs["history"].split("\n")
    assert lines[0] == "2020-01-01: made"
    assert len(lines) == 2 and "created by geozarr-pyramid-maker v" in lines[1]
    assert lines[1][:4].isdigit() and "+00:00" in lines[1]


def test_history_without_source(polar_3031, tmp_path):
    path, _, _ = _write_simple(polar_3031, tmp_path)
    assert "\n" not in _root(path).attrs["history"]


@pytest.mark.parametrize("name", FIXTURES)
def test_rioxarray_roundtrip(name, request, tmp_path):
    path, res, _ = _write(request, name, tmp_path)
    for k, lv in enumerate(res.plan.levels):
        ds = xr.open_dataset(
            path, group=str(k), engine="zarr", decode_coords="all", consolidated=False
        )
        assert ds.rio.crs == res.plan.grid.crs
        assert tuple(ds.rio.transform())[:6] == pytest.approx(lv.transform)
        for v in res.plan.grid.variables:
            assert (
                ds[v.name].attrs.get("grid_mapping", ds[v.name].encoding.get("grid_mapping"))
                == "spatial_ref"
            )


def test_grid_mapping_attr_and_scalar_spatial_ref(multi_var, tmp_path):
    path, res, _ = _write_simple(multi_var, tmp_path)
    root = _root(path)
    for k in range(len(res.plan.levels)):
        g = root[str(k)]
        for v in res.plan.grid.variables:
            assert g[v.name].attrs["grid_mapping"] == "spatial_ref"
        sr = g["spatial_ref"]
        assert sr.shape == () and sr.shards is None
        assert "GeoTransform" in sr.attrs and "crs_wkt" in sr.attrs
        assert g["x"].attrs["axis"] == "X"
        assert g["melt"].attrs["units"] == "m/yr"


def test_datatree_roundtrip(polar_3031, tmp_path):
    path, res, _ = _write_simple(polar_3031, tmp_path)
    tree = xr.open_datatree(path, engine="zarr", consolidated=True, mask_and_scale=False)
    assert sorted(tree.children) == [str(i) for i in range(len(res.plan.levels))]
    assert tree.attrs["multiscales"]["layout"][0]["asset"] == "0"


def test_consolidated_metadata_has_attrs(polar_3031, tmp_path):
    path, _, _ = _write_simple(polar_3031, tmp_path)
    meta = json.loads((path / "zarr.json").read_text())
    cm = meta["consolidated_metadata"]["metadata"]
    assert "multiscales" in meta["attributes"]
    assert "spatial:transform" in cm["1"]["attributes"]
    assert "proj:wkt2" in cm["1"]["attributes"]
    assert cm["0/melt"]["attributes"]["grid_mapping"] == "spatial_ref"


def test_level_attrs_function(polar_3031):
    _, grid = detect(polar_3031)
    plan = build_plan(grid, tile_size=32)
    a = md.level_attrs(plan, 2)
    assert a["spatial:transform"] == list(plan.levels[2].transform)
    assert a["spatial:registration"] == "pixel"


def test_gdal_zarr_driver(polar_3031, tmp_path):
    if tuple(int(p) for p in rasterio.__gdal_version__.split(".")[:2]) < (3, 13):
        pytest.skip(f"GDAL {rasterio.__gdal_version__} < 3.13 (no GeoZarr conventions support)")
    path, res, _ = _write_simple(polar_3031, tmp_path)
    with rasterio.open(f'ZARR:"{path}":/0/melt') as src:
        assert (
            src.crs.to_wkt() and pyproj.CRS.from_user_input(src.crs.to_wkt()) == res.plan.grid.crs
        )
