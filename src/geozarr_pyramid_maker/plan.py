"""Pyramid plan (PLAN 4.2): pure arithmetic on a ``GridInfo``; nothing is read or written."""

# ruff: noqa: RUF001
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from loguru import logger

from .chunking import ChunkSpec, choose_chunks, parse_size
from .detect import GridInfo

RESAMPLING_METHODS = ("mean", "nearest", "mode", "min", "max")
_ROUNDING_METHODS = ("mean", "min", "max")


@dataclass(frozen=True)
class LevelPlan:
    index: int
    shape: tuple[int, int]  # (ny, nx)
    transform: tuple[float, float, float, float, float, float]
    chunks: Mapping[str, ChunkSpec]  # per variable
    _extra_shapes: Mapping[str, tuple[int, ...]]
    _itemsizes: Mapping[str, int]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) of the (padded) level extent."""
        ny, nx = self.shape
        a, _, c, _, e, f = self.transform
        x1, y1 = c + a * nx, f + e * ny
        return (min(c, x1), min(f, y1), max(c, x1), max(f, y1))

    def var_shape(self, name: str) -> tuple[int, ...]:
        return (*self._extra_shapes[name], *self.shape)

    @property
    def nbytes(self) -> int:
        """Uncompressed size of all variables at this level."""
        return sum(math.prod(self.var_shape(n)) * self._itemsizes[n] for n in self._itemsizes)


@dataclass(frozen=True)
class PyramidPlan:
    grid: GridInfo
    resampling: Mapping[str, str]  # var -> method
    levels: tuple[LevelPlan, ...]
    tile_size: int
    shard_size: int  # bytes

    def __repr__(self) -> str:
        return _format_plan(self)

    __str__ = __repr__


def resolve_resampling(
    grid: GridInfo, resampling: str | Mapping[str, str] = "auto"
) -> dict[str, str]:
    """Resolve the per-variable resampling method (D-09, D-14)."""
    valid_vars = [v.name for v in grid.variables]
    valid_methods = ("auto", *RESAMPLING_METHODS)

    def _check(method: str) -> str:
        if method not in valid_methods:
            raise ValueError(f"Unknown resampling method {method!r}; choose from {valid_methods}.")
        return method

    if isinstance(resampling, str):
        requested = dict.fromkeys(valid_vars, _check(resampling))
    else:
        unknown = [k for k in resampling if k not in valid_vars]
        if unknown:
            raise ValueError(f"resampling has unknown variable(s) {unknown}; valid: {valid_vars}.")
        requested = {n: _check(resampling.get(n, "auto")) for n in valid_vars}

    out: dict[str, str] = {}
    for v in grid.variables:
        method = requested[v.name]
        if method == "auto":
            method = "mode" if v.categorical else "mean"
        elif v.categorical and method in _ROUNDING_METHODS:
            logger.warning(
                f"Variable {v.name!r} is categorical/integer ({v.dtype}); resampling={method!r} "
                "will round or cast values back to that dtype. Consider 'mode' or 'nearest'."
            )
        logger.debug(f"resampling for {v.name!r}: {method}")
        out[v.name] = method
    return out


def build_plan(
    grid: GridInfo,
    *,
    resampling: str | Mapping[str, str] = "auto",
    tile_size: int = 512,
    max_levels: int | None = None,
    shard_size: int | str = "128MiB",
) -> PyramidPlan:
    if tile_size < 1:
        raise ValueError(f"tile_size must be >= 1, got {tile_size}")
    if max_levels is not None and max_levels < 1:
        raise ValueError(f"max_levels must be >= 1, got {max_levels}")
    shard_bytes = parse_size(shard_size)
    methods = resolve_resampling(grid, resampling)

    extra = {v.name: tuple(v.shape[:-2]) for v in grid.variables}
    itemsize = {v.name: v.dtype.itemsize for v in grid.variables}
    a, b, c, d, e, f = grid.transform

    levels: list[LevelPlan] = []
    ny, nx = grid.shape
    k = 0
    while True:
        scale = 2**k
        level = LevelPlan(
            index=k,
            shape=(ny, nx),
            transform=(a * scale, b, c, d, e * scale, f),
            chunks={},
            _extra_shapes=extra,
            _itemsizes=itemsize,
        )
        chunks = {
            name: choose_chunks(
                level.var_shape(name), itemsize[name], tile_size=tile_size, shard_size=shard_bytes
            )
            for name in extra
        }
        object.__setattr__(level, "chunks", chunks)
        levels.append(level)
        if max(ny, nx) <= tile_size or (max_levels is not None and len(levels) >= max_levels):
            break
        ny, nx = math.ceil(ny / 2), math.ceil(nx / 2)
        k += 1

    return PyramidPlan(
        grid=grid,
        resampling=methods,
        levels=tuple(levels),
        tile_size=tile_size,
        shard_size=shard_bytes,
    )


# --------------------------------------------------------------------------- formatting


def _fmt_bytes(n: int) -> str:
    for unit, size in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def _fmt_dims(shape: tuple[int, ...]) -> str:
    *lead, y, x = shape
    spatial = f"{y}²" if y == x else f"{y}×{x}"
    return "×".join([*(str(n) for n in lead), spatial])


def _fmt_num(x: float) -> str:
    return f"{x:.6g}"


def _fmt_size(n: int) -> str:
    return _fmt_bytes(n).replace(".0 ", " ")


def _format_plan(plan: PyramidPlan) -> str:
    grid = plan.grid
    crs = grid.crs
    epsg = crs.to_epsg()
    auth = crs.to_authority()
    crs_label = f"{auth[0]}:{auth[1]}" if auth else (f"EPSG:{epsg}" if epsg else crs.name)
    extra = ", ".join(f"{k}={v}" for k, v in grid.extra_dims.items()) or "none"
    lines = [
        f"PyramidPlan: {len(plan.levels)} level(s), CRS {crs_label}",
        f"  dims: ({grid.y_dim}, {grid.x_dim})   extra dims: {extra}",
        f"  tile: {plan.tile_size} px   shard target: {_fmt_size(plan.shard_size)}",
        "  variables:",
    ]
    lines += [f"    {v.name}: {plan.resampling[v.name]}, {v.dtype}" for v in grid.variables]
    header = ("level", "shape", "resolution", "variable", "chunks", "shards", "size")
    rows: list[tuple[str, ...]] = []
    for lv in plan.levels:
        res = f"{_fmt_num(abs(lv.transform[0]))}, {_fmt_num(abs(lv.transform[4]))}"
        for i, name in enumerate(lv.chunks):
            spec = lv.chunks[name]
            first = i == 0
            rows.append(
                (
                    str(lv.index) if first else "",
                    f"{lv.shape[0]}×{lv.shape[1]}" if first else "",
                    res if first else "",
                    name,
                    _fmt_dims(spec.chunks),
                    _fmt_dims(spec.shards) if spec.shards else "-",
                    _fmt_size(lv.nbytes) if first else "",
                )
            )
    widths = [max(len(header[i]), *(len(r[i]) for r in rows)) for i in range(len(header))]

    def _row(cells: tuple[str, ...]) -> str:
        return "  " + "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()

    lines += ["", _row(header), _row(tuple("-" * w for w in widths))]
    lines += [_row(r) for r in rows]
    total = sum(lv.nbytes for lv in plan.levels)
    lines += ["", f"  total uncompressed: {_fmt_size(total)}"]
    return "\n".join(lines)
