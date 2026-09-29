"""Tests for chunk/shard arithmetic (PLAN 4.4, D-06)."""

import math

import pytest

from geozarr_pyramid_maker.chunking import ChunkSpec, choose_chunks, parse_size

MiB = 1024**2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("128MiB", 128 * MiB),
        ("64 MB", 64 * 1000**2),
        ("1GiB", 1024**3),
        ("1gb", 1000**3),
        ("512KiB", 512 * 1024),
        ("2 kb", 2000),
        ("100B", 100),
        ("100", 100),
        (4096, 4096),
        ("1.5MiB", int(1.5 * MiB)),
        ("  8 mib ", 8 * MiB),
    ],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize(
    "bad", ["", "abc", "12XB", "MiB", "-5MiB", "0", 0, -1, "1.2.3MB", None, 1.5]
)
def test_parse_size_errors(bad):
    with pytest.raises(ValueError):
        parse_size(bad)


@pytest.mark.parametrize(
    ("itemsize", "k", "nbytes"),
    [(4, 8, 64 * MiB), (8, 8, 128 * MiB), (1, 16, 64 * MiB)],
)
def test_worked_examples_rule2(itemsize, k, nbytes):
    spec = choose_chunks((100_000, 100_000), itemsize)
    assert spec.chunks == (512, 512)
    assert spec.shards == (k * 512, k * 512)
    assert spec.shard_nbytes == nbytes
    assert spec.dask_chunks == spec.shards


def test_shard_size_str_and_int_equal():
    a = choose_chunks((50_000, 50_000), 4, shard_size="16MiB")
    b = choose_chunks((50_000, 50_000), 4, shard_size=16 * MiB)
    assert a == b
    assert a.shards == (2048, 2048)  # k=4


def test_k_minimum_is_one():
    spec = choose_chunks((5000, 5000), 4, shard_size="1KiB")
    assert spec.shards == (512, 512)


def test_inner_chunk_clipped_and_extras_one():
    spec = choose_chunks((10, 3, 300, 700), 4)
    assert spec.chunks == (1, 1, 300, 512)


def test_spatial_shard_capped_at_rounded_up_level():
    # 5000 px -> 10 chunks -> 5120; k=8 would give 4096 (not capped)
    spec = choose_chunks((5000, 5000), 4)
    assert spec.shards == (4096, 4096)
    # 1100 x 1024 -> capped at 1536 x 1024
    spec = choose_chunks((1100, 1024), 4)
    assert spec.shards == (1536, 1024)


def test_rule3_time_extension():
    # 2048x2048 float32 = 16 MiB spatial shard -> 128/16 = 8 steps, capped by 5
    spec = choose_chunks((5, 2048, 2048), 4)
    assert spec.chunks == (1, 512, 512)
    assert spec.shards == (5, 2048, 2048)
    spec = choose_chunks((100, 2048, 2048), 4)
    assert spec.shards == (8, 2048, 2048)
    assert spec.shard_nbytes == 128 * MiB


def test_rule3_only_leading_dim_extended():
    spec = choose_chunks((100, 4, 2048, 2048), 4)
    assert spec.shards == (8, 1, 2048, 2048)


def test_rule3_not_applied_when_spatial_shard_partial():
    spec = choose_chunks((100, 20_000, 20_000), 4)
    assert spec.shards == (1, 4096, 4096)


def test_rule3_shard_larger_than_target_gives_one():
    spec = choose_chunks((100, 2048, 2048), 4, shard_size="1MiB")
    assert spec.shards[0] == 1


def test_rule4_cutoff():
    assert choose_chunks((100, 100), 4).shards is None
    assert choose_chunks((1024, 1024), 4).shards is None  # 4 chunks
    spec = choose_chunks((1024, 1100), 4)  # 2 x 3 = 6 chunks
    assert spec.shards is not None
    spec = choose_chunks((1100, 1100), 4)  # 3 x 3 = 9 chunks
    assert spec.shards == (1536, 1536)
    # time dim counts towards chunk total
    assert choose_chunks((5, 512, 512), 4).shards is not None
    assert choose_chunks((4, 512, 512), 4).shards is None


def test_unsharded_dask_chunks_and_nbytes():
    spec = choose_chunks((100, 100), 4)
    assert spec.dask_chunks == spec.chunks == (100, 100)
    assert spec.shard_nbytes is None
    assert spec.itemsize == 4


def test_custom_tile_size():
    spec = choose_chunks((10_000, 10_000), 4, tile_size=256)
    assert spec.chunks == (256, 256)
    # k=16 -> 4096 px, 64 MiB
    assert spec.shards == (4096, 4096)


def test_chunkspec_direct():
    spec = ChunkSpec(chunks=(1, 512, 512), shards=(2, 1024, 1024), itemsize=2)
    assert spec.dask_chunks == (2, 1024, 1024)
    assert spec.shard_nbytes == 2 * 1024 * 1024 * 2


@pytest.mark.parametrize(
    "spatial",
    [
        (1, 1),
        (1, 777),
        (513, 1025),
        (511, 512),
        (4097, 3),
        (1000, 1000),
        (65_536, 131_073),
        (100_000, 100_000),
    ],
)
@pytest.mark.parametrize("extra", [(), (7,), (3, 4), (1000,)])
@pytest.mark.parametrize("itemsize", [1, 2, 4, 8])
def test_shards_are_multiples_of_chunks(spatial, extra, itemsize):
    shape = (*extra, *spatial)
    spec = choose_chunks(shape, itemsize)
    assert len(spec.chunks) == len(shape)
    assert all(1 <= c <= s for c, s in zip(spec.chunks, shape, strict=True))
    if spec.shards is None:
        assert math.prod(math.ceil(s / c) for s, c in zip(shape, spec.chunks, strict=True)) <= 4
        return
    assert len(spec.shards) == len(shape)
    for s, c, n in zip(spec.shards, spec.chunks, shape, strict=True):
        assert s % c == 0
        assert s <= math.ceil(n / c) * c
    assert math.prod(math.ceil(s / c) for s, c in zip(shape, spec.chunks, strict=True)) > 4
    assert spec.dask_chunks == spec.shards
    assert spec.shard_nbytes == math.prod(spec.shards) * itemsize


def test_no_loguru_handlers_added():
    from loguru import logger

    before = dict(logger._core.handlers)
    choose_chunks((2000, 2000), 4)
    assert dict(logger._core.handlers) == before
