"""Tests for validate.py. Pyramids are built here directly, independent of write.py/metadata.py."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rioxarray  # noqa: F401
import xarray as xr
import zarr
from geozarr_toolkit import (
    MultiscalesConventionMetadata,
    ProjConventionMetadata,
    SpatialConventionMetadata,
    create_multiscales_layout,
    create_proj_attrs,
    create_spatial_attrs,
    create_zarr_conventions,
)

from geozarr_pyramid_maker import build_plan, detect, is_valid, validate
from geozarr_pyramid_maker.write import to_pyramid

N = 40
KEYS = {"zarr_conventions", "multiscales", "spatial", "proj", "structure"}


def _level_ds(k: int, variables=("v",)) -> xr.Dataset:
    n = N // 2**k
    res = 10.0 * 2**k
    x = 1000.0 + res / 2 + res * np.arange(n)
    y = 5000.0 - res / 2 - res * np.arange(n)
    data = {
        v: (("y", "x"), np.full((n, n), i + 1, dtype="float32")) for i, v in enumerate(variables)
    }
    return xr.Dataset(data, coords={"x": x, "y": y})


def _level_attrs(k: int) -> dict:
    n = N // 2**k
    res = 10.0 * 2**k
    return create_spatial_attrs(
        ["y", "x"],
        transform=[res, 0.0, 1000.0, 0.0, -res, 5000.0],
        bbox=[1000.0, 5000.0 - res * n, 1000.0 + res * n, 5000.0],
        shape=[n, n],
    )


def make_pyramid(store, *, level1_vars=("v",), plan=None) -> None:
    for k in (0, 1):
        ds = _level_ds(k, ("v",) if k == 0 else level1_vars)
        n = N // 2**k
        enc = {v: {"chunks": (n // 2, n // 2), "shards": (n, n)} for v in ds.data_vars}
        if plan is not None:
            enc = {
                v: {
                    "chunks": plan.levels[k].chunks[v].chunks,
                    "shards": plan.levels[k].chunks[v].shards,
                }
                for v in ds.data_vars
            }
        ds.to_zarr(
            store,
            group=str(k),
            mode="w" if k == 0 else "a",
            zarr_format=3,
            encoding=enc,
            consolidated=False,
        )
    root = zarr.open_group(store, mode="r+")
    for k in (0, 1):
        root[str(k)].attrs.update(_level_attrs(k))
    attrs = dict(_level_attrs(0))
    attrs.update(create_proj_attrs(code="EPSG:3031"))
    attrs.update(
        create_multiscales_layout(
            [
                {"asset": "0"},
                {
                    "asset": "1",
                    "derived_from": "0",
                    "transform": {"scale": [2.0, 2.0], "translation": [0.0, 0.0]},
                },
            ],
            resampling_method="average",
        )
    )
    attrs["zarr_conventions"] = create_zarr_conventions(
        MultiscalesConventionMetadata(), SpatialConventionMetadata(), ProjConventionMetadata()
    )
    root.attrs.update(attrs)


@pytest.fixture
def pyramid(tmp_path) -> Path:
    path = tmp_path / "p.zarr"
    make_pyramid(str(path))
    return path


def _set(path, group, **attrs):
    g = zarr.open_group(str(path), mode="r+")
    if group:
        g = g[group]
    g.attrs.update(attrs)


def _grid(ds):
    return detect(ds.rio.write_crs("EPSG:3031"))[1]


def _plan(tile_size=20):
    return build_plan(_grid(_level_ds(0)), tile_size=tile_size)


def test_valid_pyramid(pyramid):
    report = validate(str(pyramid))
    assert set(report) == KEYS
    assert all(v == [] for v in report.values()), report
    assert is_valid(report)


@pytest.mark.parametrize("kind", ["str", "path", "memory"])
def test_store_kinds(tmp_path, kind):
    if kind == "memory":
        store = zarr.storage.MemoryStore()
        make_pyramid(store)
    else:
        path = tmp_path / "p.zarr"
        make_pyramid(str(path))
        store = str(path) if kind == "str" else path
    assert is_valid(validate(store))


def test_missing_store(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate(str(tmp_path / "nope.zarr"))
    with pytest.raises(FileNotFoundError):
        validate(tmp_path / "nope.zarr")


def test_missing_level_group(pyramid):
    root = zarr.open_group(str(pyramid), mode="r+")
    ms = dict(root.attrs["multiscales"])
    ms["layout"] = [
        *ms["layout"],
        {
            "asset": "2",
            "derived_from": "1",
            "transform": {"scale": [2.0, 2.0], "translation": [0.0, 0.0]},
        },
    ]
    root.attrs["multiscales"] = ms
    report = validate(str(pyramid))
    assert any("asset '2'" in m for m in report["structure"])
    assert not is_valid(report)


def test_wrong_spatial_shape(pyramid):
    _set(pyramid, "1", **{"spatial:shape": [19, 20]})
    report = validate(pyramid)
    assert any("level 1" in m and "spatial:shape" in m for m in report["structure"])


def test_bad_transform(pyramid):
    _set(pyramid, "1", **{"spatial:transform": [20.0, 0.0, 1000.0, 0.0, -20.0]})
    report = validate(pyramid)
    assert report["spatial"] and report["spatial"][0].startswith("level 1:")


def test_missing_proj(pyramid):
    root = zarr.open_group(str(pyramid), mode="r+")
    del root.attrs["proj:code"]
    report = validate(pyramid)
    assert report["proj"] and report["proj"][0].startswith("root:")


def test_missing_proj_and_declaration(pyramid):
    root = zarr.open_group(str(pyramid), mode="r+")
    del root.attrs["proj:code"]
    root.attrs["zarr_conventions"] = [
        c for c in root.attrs["zarr_conventions"] if c.get("name") != "proj:"
    ]
    report = validate(pyramid)
    assert "root: no proj:* attribute (convention required)" in report["proj"]
    assert any("not declared in zarr_conventions" in m for m in report["proj"])
    assert any(m.startswith("level 1: no proj:*") for m in report["proj"])


def test_level_missing_required_spatial(pyramid):
    g = zarr.open_group(str(pyramid), mode="r+")["1"]
    del g.attrs["spatial:transform"]
    report = validate(pyramid)
    assert any("level 1: no spatial:transform" in m for m in report["spatial"])


def test_derived_from_later_level(pyramid):
    root = zarr.open_group(str(pyramid), mode="r+")
    ms = dict(root.attrs["multiscales"])
    layout = [dict(lv) for lv in ms["layout"]]
    layout[0]["derived_from"] = "1"
    layout[0]["transform"] = {"scale": [1.0, 1.0], "translation": [0.0, 0.0]}
    ms["layout"] = layout
    root.attrs["multiscales"] = ms
    report = validate(pyramid)
    assert any("derived_from" in m and "level 0" in m for m in report["structure"])


def test_variable_missing_in_level1(tmp_path):
    path = tmp_path / "p.zarr"
    make_pyramid(str(path), level1_vars=("w",))
    report = validate(path)
    assert any("level 1" in m and "differ from level 0" in m for m in report["structure"])


def test_plan_matches(tmp_path):
    plan = _plan()
    pyramid = tmp_path / "planned.zarr"
    make_pyramid(str(pyramid), plan=plan)
    assert len(plan.levels) == 2
    report = validate(pyramid, plan=plan)
    assert is_valid(report), report


def test_plan_mismatch(pyramid):
    plan = _plan()
    _set(pyramid, "1", **{"spatial:transform": [21.0, 0.0, 1000.0, 0.0, -21.0, 5000.0]})
    msgs = validate(pyramid, plan=plan)["structure"]
    assert any(m.startswith("plan:") and "level 1" in m and "transform" in m for m in msgs)


def test_plan_chunk_mismatch(pyramid):
    plan = _plan(tile_size=10)  # different chunking than the store
    msgs = validate(pyramid, plan=plan)["structure"]
    assert any("chunks" in m or "shards" in m for m in msgs)


def test_plan_level_count_mismatch(pyramid):
    plan = _plan(tile_size=100)
    assert len(plan.levels) == 1
    report = validate(pyramid, plan=plan)
    assert any("level(s)" in m for m in report["structure"])


def test_logging_summary(pyramid, log_records):
    validate(pyramid)
    assert ("INFO", "validation passed") in log_records
    _set(pyramid, "1", **{"spatial:shape": [1, 1]})
    validate(pyramid)
    assert any(lvl == "INFO" and m.startswith("validation found") for lvl, m in log_records)


def test_integration_to_pyramid(tmp_path, polar_3031):
    out = tmp_path / "out.zarr"
    to_pyramid(polar_3031, str(out), tile_size=64, validate=False)
    report = validate(str(out))
    assert is_valid(report), report
