"""Tests for write.py (M2): data-only pyramid writing."""

import json
import warnings

import numpy as np
import obstore.store
import pytest
import xarray as xr
import zarr
from zarr.storage import LocalStore, ObjectStore

import geozarr_pyramid_maker as gpm
from geozarr_pyramid_maker import write as write_mod
from geozarr_pyramid_maker.detect import detect
from geozarr_pyramid_maker.resample import downsample
from geozarr_pyramid_maker.write import PyramidResult, _resolve_store, to_pyramid

FIXTURES = [
    "global_4326_0360",
    "regional_utm",
    "polar_3031",
    "multi_var",
    "tiny",
    "regional_antimeridian",
]


def _open_level(path, k, **kw):
    return xr.open_zarr(path, group=str(k), consolidated=False, mask_and_scale=False, **kw)


def _fixture_kwargs(name):
    return {"tile_size": 64} if name in ("regional_utm", "global_4326_0360") else {"tile_size": 32}


@pytest.mark.parametrize("name", FIXTURES)
def test_roundtrip_structure(name, request, tmp_path):
    ds = request.getfixturevalue(name)
    path = tmp_path / "out.zarr"
    res = to_pyramid(ds, str(path), validate=False, **_fixture_kwargs(name))
    assert isinstance(res, PyramidResult)
    plan = res.plan
    tree = xr.open_datatree(path, engine="zarr", consolidated=True, mask_and_scale=False)
    assert sorted(k for k in tree.children) == [str(i) for i in range(len(plan.levels))]
    grid = plan.grid
    for lv in plan.levels:
        node = tree[str(lv.index)].to_dataset()
        assert node.sizes[grid.y_dim] == lv.shape[0]
        assert node.sizes[grid.x_dim] == lv.shape[1]
        for d, n in grid.extra_dims.items():
            assert node.sizes[d] == n
        for v in grid.variables:
            assert node[v.name].dtype == v.dtype
            assert node[v.name].shape == lv.var_shape(v.name)
            arr = zarr.open_array(LocalStore(path), path=f"{lv.index}/{v.name}", mode="r")
            spec = lv.chunks[v.name]
            assert arr.chunks == spec.chunks
            assert arr.shards == spec.shards
    assert set(res.timings) == {*(f"level_{i}" for i in range(len(plan.levels))), "total"}
    assert res.validation is None  # validate=False


def test_multi_level_exists(polar_3031, tmp_path):
    res = to_pyramid(polar_3031, str(tmp_path / "o.zarr"), tile_size=64)
    assert len(res.plan.levels) >= 3


@pytest.mark.parametrize("name", FIXTURES)
def test_level0_equals_normalised_source(name, request, tmp_path):
    ds = request.getfixturevalue(name)
    norm, grid = detect(ds)
    path = tmp_path / "o.zarr"
    to_pyramid(ds, str(path), **_fixture_kwargs(name))
    lvl = _open_level(path, 0)
    for v in grid.variables:
        np.testing.assert_array_equal(lvl[v.name].values, norm[v.name].values)
    np.testing.assert_allclose(lvl[grid.x_dim].values, norm[grid.x_dim].values)
    np.testing.assert_allclose(lvl[grid.y_dim].values, norm[grid.y_dim].values)


def test_nan_masked_fill(regional_utm, tmp_path):
    path = tmp_path / "o.zarr"
    to_pyramid(regional_utm, str(path), tile_size=64)
    dem = _open_level(path, 0).dem.values
    assert np.isnan(dem[0, 0]) and np.isnan(dem[500, 400])
    assert not (dem == -9999).any()
    fv = zarr.open_array(LocalStore(path), path="0/dem", mode="r").fill_value
    assert np.isnan(fv)


def test_antimeridian_and_roll(global_4326_0360, regional_antimeridian, tmp_path):
    for i, ds in enumerate((global_4326_0360, regional_antimeridian)):
        norm, _ = detect(ds)
        to_pyramid(ds, str(tmp_path / f"o{i}.zarr"), tile_size=64)
        lvl = _open_level(tmp_path / f"o{i}.zarr", 0)
        np.testing.assert_array_equal(lvl.sst.values, norm.sst.values)
        assert float(lvl.lon.min()) < 0  # rolled to -180..180


def test_level1_equals_downsample(polar_3031, tmp_path):
    path = tmp_path / "o.zarr"
    res = to_pyramid(polar_3031, str(path), tile_size=64)
    norm, grid = detect(polar_3031)
    fills = {v.name: v.fill_value for v in grid.variables}
    expected = downsample(
        norm, x_dim=grid.x_dim, y_dim=grid.y_dim, methods=res.plan.resampling, fill_values=fills
    ).compute()
    got = _open_level(path, 1)
    np.testing.assert_allclose(got.melt.values, expected.melt.values, equal_nan=True)
    np.testing.assert_allclose(got.x.values, expected.x.values)
    # hand-computed: mean of the 2x2 block at (0,0); value = row*1000 + col
    assert got.melt.values[0, 0] == pytest.approx((0 + 1 + 1000 + 1001) / 4)


def test_int_mask_mode_and_fill(multi_var, tmp_path):
    path = tmp_path / "o.zarr"
    res = to_pyramid(multi_var, str(path), tile_size=16)
    assert res.plan.resampling["mask"] == "mode"
    for k in range(len(res.plan.levels)):
        arr = zarr.open_array(LocalStore(path), path=f"{k}/mask", mode="r")
        assert arr.dtype == np.dtype("int8")
        assert arr.fill_value == -1
    norm, _ = detect(multi_var)
    lvl1 = _open_level(path, 1).mask.values
    m = norm.mask.values
    # block (1, 1) covers rows 2-3, cols 2-3; the mode must be one of the block's values
    block = m[2:4, 2:4].ravel()
    assert lvl1[1, 1] in block
    lvl0 = _open_level(path, 0).mask.values
    np.testing.assert_array_equal(lvl0, m)


def test_overwrite(polar_3031, tmp_path):
    path = tmp_path / "o.zarr"
    to_pyramid(polar_3031, str(path), tile_size=64)
    snapshot = sorted(
        (p.relative_to(path), p.stat().st_mtime_ns) for p in path.rglob("*") if p.is_file()
    )
    with pytest.raises(FileExistsError):
        to_pyramid(polar_3031, str(path), tile_size=64)
    after = sorted(
        (p.relative_to(path), p.stat().st_mtime_ns) for p in path.rglob("*") if p.is_file()
    )
    assert snapshot == after

    # overwrite=True replaces the store (fewer levels now)
    res = to_pyramid(polar_3031, str(path), tile_size=512, overwrite=True)
    assert len(res.plan.levels) == 1
    assert sorted(zarr.open_group(LocalStore(path), mode="r").group_keys()) == ["0"]


def test_overwrite_warns(polar_3031, tmp_path, log_records):
    path = str(tmp_path / "o.zarr")
    to_pyramid(polar_3031, path, tile_size=512)
    to_pyramid(polar_3031, path, tile_size=512, overwrite=True)
    assert any(lvl == "WARNING" and "Overwriting" in msg for lvl, msg in log_records)


def test_numpy_input(numpy_backed, tmp_path, log_records):
    path = tmp_path / "o.zarr"
    res = to_pyramid(numpy_backed, str(path), tile_size=64)
    assert any(lvl == "INFO" and "not dask" in msg for lvl, msg in log_records)
    lvl = _open_level(path, 0)
    np.testing.assert_array_equal(lvl.melt.values, numpy_backed.melt.values)
    assert len(res.plan.levels) >= 2


def test_dataarray_via_accessor(polar_3031, tmp_path):
    path = tmp_path / "o.zarr"
    res = polar_3031.melt.geozarr.to_pyramid(str(path), tile_size=64)
    assert isinstance(res, gpm.PyramidResult)
    assert "melt" in _open_level(path, 0)


def test_dataset_accessor_path_object(polar_3031, tmp_path):
    res = polar_3031.geozarr.to_pyramid(tmp_path / "p.zarr", tile_size=512)
    assert res.store == str(tmp_path / "p.zarr")


def test_objectstore_instance(polar_3031):
    store = ObjectStore(obstore.store.MemoryStore(), read_only=False)
    res = to_pyramid(polar_3031, store, tile_size=64)
    assert res.store is store
    root = zarr.open_group(store, mode="r")
    assert sorted(root.group_keys()) == [str(i) for i in range(len(res.plan.levels))]
    ds = xr.open_zarr(store, group="1", consolidated=False)
    assert ds.melt.shape == res.plan.levels[1].var_shape("melt")


def test_resolve_store_kinds(tmp_path):
    assert isinstance(_resolve_store(str(tmp_path / "a.zarr")), LocalStore)
    assert isinstance(_resolve_store(tmp_path / "a.zarr"), LocalStore)
    assert isinstance(_resolve_store("memory:///"), ObjectStore)
    store = LocalStore(tmp_path / "b")
    assert _resolve_store(store) is store
    with pytest.raises(TypeError):
        _resolve_store(123)


def test_resolve_store_fsspec_fallbacks(tmp_path, monkeypatch):
    fsspec_store = _resolve_store(f"file://{tmp_path}/c.zarr")
    assert isinstance(fsspec_store, zarr.storage.FsspecStore)
    # no obstore, no fsspec -> ImportError with hint
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name.split(".")[0] in ("obstore", "fsspec"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(ImportError, match=r"geozarr-pyramid-maker\[cloud\]"):
        _resolve_store("s3://bucket/x.zarr")


def test_memory_url_store_does_not_persist():
    """Documented finding: separate from_url('memory:///') calls give independent stores."""
    a = _resolve_store("memory:///")
    zarr.open_group(a, mode="w", path="g")
    b = _resolve_store("memory:///")
    with pytest.raises(FileNotFoundError):
        zarr.open_group(b, mode="r")


def test_memory_url_end_to_end_runs(tiny):
    # the URL path works for writing even though the store cannot be reopened afterwards
    res = to_pyramid(tiny, "memory:///", tile_size=64)
    assert len(res.plan.levels) == 2


def test_consolidated_metadata_and_no_warning(polar_3031, tmp_path):
    path = tmp_path / "o.zarr"
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        to_pyramid(polar_3031, str(path), tile_size=64)
    assert not [w for w in rec if "onsolidated" in str(w.message)]
    meta = json.loads((path / "zarr.json").read_text())
    assert "consolidated_metadata" in meta
    assert {"0", "1"} <= set(meta["consolidated_metadata"]["metadata"])
    root = zarr.open_group(LocalStore(path), mode="r", use_consolidated=True)
    assert "0" in root


def test_sharded_large_roundtrip(tmp_path, monkeypatch):
    n = 1100
    rng = np.random.default_rng(0)
    data = rng.random((n, n), dtype="float32")
    ds = xr.Dataset(
        {"v": (("y", "x"), data)},
        coords={"x": 1000.0 + np.arange(n) * 10.0, "y": 50_000.0 - np.arange(n) * 10.0},
    ).rio.write_crs("EPSG:32632")

    seen = []
    orig = xr.Dataset.to_zarr

    def spy(self, *a, **k):
        seen.append((k.get("group"), {v: self[v].chunks for v in self.data_vars}, k["encoding"]))
        return orig(self, *a, **k)

    monkeypatch.setattr(xr.Dataset, "to_zarr", spy)
    path = tmp_path / "big.zarr"
    res = to_pyramid(ds, str(path), tile_size=256, shard_size="1MiB")
    spec = res.plan.levels[0].chunks["v"]
    assert spec.shards is not None
    assert seen[0][0] == "0"
    for (_group, chunks, enc), lv in zip(seen, res.plan.levels, strict=True):
        s = lv.chunks["v"]
        # dask chunks equal the shard (or chunk) shape, except for the ragged last block
        for dim_chunks, size in zip(chunks["v"], s.dask_chunks, strict=True):
            assert all(c == size for c in dim_chunks[:-1]) and dim_chunks[-1] <= size
        assert enc["v"].get("shards") == s.shards
    arr = zarr.open_array(LocalStore(path), path="0/v", mode="r")
    assert arr.shards == spec.shards
    np.testing.assert_array_equal(_open_level(path, 0).v.values, data)
    # level 1 is a 2x2 mean
    got = _open_level(path, 1).v.values
    m = n // 2
    exp = (
        data[: 2 * m, : 2 * m]
        .reshape(m, 2, m, 2)
        .mean(axis=(1, 3), dtype="float64")
        .astype("float32")
    )
    np.testing.assert_allclose(got[:m, :m], exp, rtol=1e-6)
    assert got.shape == res.plan.levels[1].shape


def test_zstd_level_configured(polar_3031, tmp_path):
    path = tmp_path / "o.zarr"
    to_pyramid(polar_3031, str(path), tile_size=64, compression_level=7)
    arr = zarr.open_array(LocalStore(path), path="0/melt", mode="r")
    zstd = [c for c in arr.compressors if isinstance(c, zarr.codecs.ZstdCodec)]
    assert zstd and zstd[0].level == 7


def test_reopens_once_per_level(polar_3031, tmp_path, monkeypatch):
    calls = []
    orig = xr.open_zarr

    def spy(*a, **k):
        calls.append(k.get("group"))
        return orig(*a, **k)

    monkeypatch.setattr(xr, "open_zarr", spy)
    res = to_pyramid(polar_3031, str(tmp_path / "o.zarr"), tile_size=64)
    assert calls == [str(k) for k in range(len(res.plan.levels) - 1)]


def test_detection_error_before_writing(curvilinear, tmp_path):
    path = tmp_path / "o.zarr"
    with pytest.raises(gpm.DetectionError):
        to_pyramid(curvilinear, str(path))
    assert not path.exists()


def test_preview_is_noop(polar_3031, tmp_path, log_records):
    to_pyramid(polar_3031, str(tmp_path / "o.zarr"), tile_size=512, validate=False, preview=True)
    assert any(lvl == "WARNING" and "M5" in msg for lvl, msg in log_records)


def test_validation_report_stored_and_logged(polar_3031, tmp_path, log_records):
    pytest.importorskip("geozarr_pyramid_maker.validate")
    res = to_pyramid(polar_3031, str(tmp_path / "o.zarr"), tile_size=64, validate=True)
    assert res.validation is not None
    assert all(not errs for errs in res.validation.values())
    assert any(lvl == "INFO" and "validation passed" in msg for lvl, msg in log_records)


def test_validation_failure_logged_not_raised(polar_3031, tmp_path, log_records, monkeypatch):
    pytest.importorskip("geozarr_pyramid_maker.validate")
    import sys

    vmod = sys.modules["geozarr_pyramid_maker.validate"]  # package attr may be the function

    monkeypatch.setattr(vmod, "validate", lambda *a, **k: {"spatial": ["boom"], "structure": []})
    res = to_pyramid(polar_3031, str(tmp_path / "o.zarr"), tile_size=512, validate=True)
    assert res.validation == {"spatial": ["boom"], "structure": []}
    assert any(lvl == "ERROR" and "boom" in msg for lvl, msg in log_records)


def test_refuses_to_delete_foreign_dir(polar_3031, tmp_path):
    target = tmp_path / "precious"
    target.mkdir()
    (target / "notes.txt").write_text("keep me")
    with pytest.raises(FileExistsError, match="not a Zarr store; refusing to delete it"):
        to_pyramid(polar_3031, str(target), tile_size=512, overwrite=True)
    assert (target / "notes.txt").read_text() == "keep me"


def test_refuses_to_write_into_foreign_dir(polar_3031, tmp_path):
    target = tmp_path / "precious"
    target.mkdir()
    (target / "notes.txt").write_text("keep me")
    with pytest.raises(FileExistsError, match="not a Zarr store"):
        to_pyramid(polar_3031, str(target), tile_size=512)
    assert sorted(p.name for p in target.iterdir()) == ["notes.txt"]


def test_empty_existing_dir_is_fine(polar_3031, tmp_path):
    target = tmp_path / "empty"
    target.mkdir()
    to_pyramid(polar_3031, str(target), tile_size=512, validate=False)
    assert (target / "zarr.json").exists()


def test_write_module_public():
    assert write_mod.to_pyramid is to_pyramid
