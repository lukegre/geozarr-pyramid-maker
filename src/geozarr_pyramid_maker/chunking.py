"""Chunk and shard arithmetic (PLAN 4.4, D-06). Pure arithmetic: no xarray, no I/O."""

import math
import re
from dataclasses import dataclass

from loguru import logger

_UNITS: dict[str, int] = {
    "": 1,
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-z]*)\s*$", re.IGNORECASE)
_MAX_UNSHARDED_CHUNKS = 4


def parse_size(size: int | str) -> int:
    """'128MiB' / '64 MB' / '1GiB' / '512KiB' / int bytes -> int bytes.

    Binary (KiB/MiB/GiB) = 1024**n, decimal (KB/MB/GB) = 1000**n, 'B' or bare int.
    Case-insensitive. ValueError on garbage or <= 0.
    """
    if isinstance(size, bool) or not isinstance(size, (int, str)):
        raise ValueError(f"Invalid size: {size!r}")
    if isinstance(size, int):
        value = size
    else:
        match = _SIZE_RE.match(size)
        if match is None or match.group(2).lower() not in _UNITS:
            raise ValueError(f"Invalid size: {size!r}")
        value = int(float(match.group(1)) * _UNITS[match.group(2).lower()])
    if value <= 0:
        raise ValueError(f"Size must be positive: {size!r}")
    return value


@dataclass(frozen=True)
class ChunkSpec:
    """Inner chunk shape and optional shard shape (same dim order as the array)."""

    chunks: tuple[int, ...]
    shards: tuple[int, ...] | None
    itemsize: int = 1

    @property
    def dask_chunks(self) -> tuple[int, ...]:
        """Dask chunk shape: the shard shape if sharded, else the inner chunks."""
        return self.shards if self.shards is not None else self.chunks

    @property
    def shard_nbytes(self) -> int | None:
        """Uncompressed bytes per shard, or None if unsharded."""
        if self.shards is None:
            return None
        return math.prod(self.shards) * self.itemsize


def choose_chunks(
    shape: tuple[int, ...],
    itemsize: int,
    *,
    tile_size: int = 512,
    shard_size: int | str = "128MiB",
) -> ChunkSpec:
    """Choose inner chunks and shards for an array of ``shape`` (spatial dims last)."""
    if len(shape) < 2:
        raise ValueError(f"shape needs at least 2 dims, got {shape}")
    if any(n < 1 for n in shape):
        raise ValueError(f"shape dims must be >= 1, got {shape}")
    if itemsize < 1 or tile_size < 1:
        raise ValueError("itemsize and tile_size must be >= 1")
    shard_bytes = parse_size(shard_size)

    *extra, ny, nx = shape
    chunk_y, chunk_x = min(tile_size, ny), min(tile_size, nx)
    chunks = (*([1] * len(extra)), chunk_y, chunk_x)

    n_chunks = math.prod(math.ceil(n / c) for n, c in zip(shape, chunks, strict=True))
    if n_chunks <= _MAX_UNSHARDED_CHUNKS:
        logger.trace(
            "chunking: shape={} chunks={} n_chunks={} -> no shard", shape, chunks, n_chunks
        )
        return ChunkSpec(chunks=chunks, shards=None, itemsize=itemsize)

    k = 1
    while (2 * k) ** 2 * tile_size**2 * itemsize <= shard_bytes:
        k *= 2
    shard_y = min(k * tile_size, math.ceil(ny / chunk_y) * chunk_y)
    shard_x = min(k * tile_size, math.ceil(nx / chunk_x) * chunk_x)
    lead = [1] * len(extra)
    if extra and shard_y >= ny and shard_x >= nx:
        spatial_bytes = shard_y * shard_x * itemsize
        lead[0] = min(extra[0], max(1, shard_bytes // spatial_bytes))
    shards = (*lead, shard_y, shard_x)

    assert all(s % c == 0 for s, c in zip(shards, chunks, strict=True)), (shards, chunks)
    logger.trace(
        "chunking: shape={} itemsize={} tile={} shard_bytes={} k={} chunks={} shards={}",
        shape,
        itemsize,
        tile_size,
        shard_bytes,
        k,
        chunks,
        shards,
    )
    return ChunkSpec(chunks=chunks, shards=shards, itemsize=itemsize)
