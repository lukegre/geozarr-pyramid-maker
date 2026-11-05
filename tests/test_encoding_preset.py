"""Tests for encoding_preset="web" (D-63)."""

import json
import math

import numpy as np
import pytest
import xarray as xr

import geozarr_pyramid_maker  # noqa: F401  (registers the .geozarr accessor)
from geozarr_pyramid_maker.write import to_pyramid

SHARDS = {"time": -1, "y": 512, "x": 512}


def _make(dtype="float32", nan=False, n=512):
    rng = np.random.default_rng(1)
    if np.dtype(dtype).kind == "f":
        data = (rng.random((3, n, n)) * 1000 + 1).astype(dtype)
        if nan:
            data[:, 5:9, 5:9] = np.nan
    else:
        data = rng.integers(1, 100, (3, n, n)).astype(dtype)
    ds = xr.Dataset(
        {"v": (("time", "y", "x"), data)},
        coords={
            "time": np.array(["2026-01-01", "2026-01-02", "2026-01-03"], dtype="datetime64[ns]"),
            "x": 500000.0 + 100.0 * (np.arange(n) + 0.5),
            "y": 5000000.0 - 100.0 * (np.arange(n) + 0.5),
        },
    )
    return ds.rio.write_crs("EPSG:32632")


def _zjson(path, k=0, name="v"):
    return json.loads((path / str(k) / name / "zarr.json").read_text())


def _tree(path):
    return {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def _strip_history(raw):
    """Parse zarr.json and drop every attributes.history (also inside consolidated metadata)."""

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items() if k != "history"}
        return [walk(v) for v in o] if isinstance(o, list) else o

    return walk(json.loads(raw))


def _codecs(meta):
    return meta["codecs"]


def _shard_offsets(path, res, k=0):
    """Offsets of the non-empty inner chunks of the first shard, in index (C) order."""
    spec = res.plan.levels[k].chunks["v"]
    n = math.prod(math.ceil(s / c) for s, c in zip(spec.shards, spec.chunks, strict=True))
    f = path / str(k) / "v" / "c" / "0" / "0" / "0"
    raw = f.read_bytes()
    idx = np.frombuffer(raw[len(raw) - (n * 16 + 4) : -4], dtype="<u8").reshape(n, 2)
    return [int(o) for o, _ in idx if o != 2**64 - 1]


def test_default_preset_is_byte_identical(tmp_path):
    ds = _make()
    a, b = tmp_path / "a.zarr", tmp_path / "b.zarr"
    to_pyramid(ds, str(a), tile_size=256, shard_size=SHARDS)
    to_pyramid(ds, str(b), tile_size=256, shard_size=SHARDS, encoding_preset="default")
    ta, tb = _tree(a), _tree(b)
    assert ta.keys() == tb.keys()
    for k in ta:
        if k.endswith("zarr.json"):  # "history" carries a 1 s timestamp; ignore it
            assert _strip_history(ta[k]) == _strip_history(tb[k]), k
        else:
            assert ta[k] == tb[k], k
    names = json.dumps(_zjson(a)["codecs"])
    assert "zstd" in names and "blosc" not in names


def test_web_codecs_and_roundtrip(tmp_path):
    ds = _make()
    out = tmp_path / "w.zarr"
    res = to_pyramid(ds, str(out), tile_size=256, shard_size=SHARDS, encoding_preset="web")
    assert res.validation is not None
    assert not [e for errs in res.validation.values() for e in errs]
    codecs = _codecs(_zjson(out))
    assert len(codecs) == 1 and codecs[0]["name"] == "sharding_indexed"
    cfg = codecs[0]["configuration"]
    assert [c["name"] for c in cfg["codecs"]] == ["bytes", "blosc"]
    blosc = cfg["codecs"][1]["configuration"]
    assert blosc["shuffle"] == "bitshuffle" and blosc["typesize"] == 4
    assert blosc["cname"] == "zstd" and blosc["clevel"] == 3
    assert [c["name"] for c in cfg["index_codecs"]] == ["bytes", "crc32c"]
    assert cfg["index_location"] == "end"
    got = xr.open_zarr(out, group="0", consolidated=False)["v"].values
    np.testing.assert_array_equal(got, ds.v.values)


def test_web_shard_order_is_lexicographic(tmp_path):
    ds = _make()
    web, dflt = tmp_path / "w.zarr", tmp_path / "d.zarr"
    rw = to_pyramid(ds, str(web), tile_size=256, shard_size=SHARDS, encoding_preset="web")
    rd = to_pyramid(ds, str(dflt), tile_size=256, shard_size=SHARDS)
    spec = rw.plan.levels[0].chunks["v"]
    assert spec.shards is not None and len(spec.chunks) == 3
    assert spec.shards[1] == spec.shards[2] == 512 and spec.chunks[1] == 256
    ow = _shard_offsets(web, rw)
    assert len(ow) == math.prod(s // c for s, c in zip(spec.shards, spec.chunks, strict=True))
    assert ow == sorted(ow) and len(set(ow)) == len(ow)
    od = _shard_offsets(dflt, rd)
    assert od != sorted(od)  # zarr's default is Morton order


def test_web_keepbits(tmp_path):
    ds = _make(nan=True)
    out = tmp_path / "k.zarr"
    to_pyramid(ds, str(out), tile_size=256, shard_size=SHARDS, keepbits=10, encoding_preset="web")
    got = xr.open_zarr(out, group="0", consolidated=False)["v"].values
    orig = ds.v.values
    np.testing.assert_array_equal(np.isnan(got), np.isnan(orig))
    ok = ~np.isnan(orig)
    assert np.max(np.abs(got[ok] - orig[ok]) / np.abs(orig[ok])) <= 2.0**-10


@pytest.mark.parametrize("dtype", ["int16", "uint8"])
def test_web_integer_uses_byte_shuffle(dtype, tmp_path):
    ds = _make(dtype)
    out = tmp_path / "i.zarr"
    to_pyramid(ds, str(out), tile_size=256, shard_size=SHARDS, encoding_preset="web")
    inner = _codecs(_zjson(out))[0]["configuration"]["codecs"][1]["configuration"]
    assert inner["shuffle"] == "shuffle" and inner["typesize"] == np.dtype(dtype).itemsize
    got = xr.open_zarr(out, group="0", consolidated=False, mask_and_scale=False)["v"].values
    np.testing.assert_array_equal(got, ds.v.values)


def test_web_unsharded_uses_blosc_compressor(tmp_path):
    ds = _make(n=64)
    out = tmp_path / "u.zarr"
    res = to_pyramid(ds, str(out), tile_size=32, shard_size=32, encoding_preset="web")
    spec = res.plan.levels[0].chunks["v"]
    meta = _zjson(out)
    assert spec.shards is None
    assert [c["name"] for c in meta["codecs"]] == ["bytes", "blosc"]
    np.testing.assert_array_equal(
        xr.open_zarr(out, group="0", consolidated=False)["v"].values, ds.v.values
    )


def test_invalid_preset_and_level(tmp_path):
    ds = _make(n=64)
    with pytest.raises(ValueError, match="encoding_preset"):
        to_pyramid(ds, str(tmp_path / "a.zarr"), encoding_preset="fast")
    with pytest.raises(ValueError, match="compression_level"):
        to_pyramid(ds, str(tmp_path / "b.zarr"), encoding_preset="web", compression_level=12)
    assert not (tmp_path / "a.zarr").exists() and not (tmp_path / "b.zarr").exists()


def test_accessor_forwards_preset(tmp_path):
    ds = _make(n=64)
    out = tmp_path / "acc.zarr"
    ds.geozarr.to_pyramid(str(out), tile_size=32, encoding_preset="web")
    assert "blosc" in json.dumps(_zjson(out))
