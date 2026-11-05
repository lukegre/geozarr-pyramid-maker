"""Pyramid plan (PLAN 4.2): pure arithmetic on a ``GridInfo``; nothing is read or written."""

# ruff: noqa: RUF001
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from loguru import logger

from .chunking import AUTO_SHARD_BYTES, ChunkSpec, choose_chunks, fixed_chunks
from .detect import GridInfo, VarInfo

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
    tile_size: Mapping[str, int]  # var -> tile edge in pixels
    shard_size: Mapping[str, Mapping[str, int] | None]  # var -> dim -> px, None = automatic

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
                "will round or cast values back to that dtype (consider 'mode' or 'nearest')"
            )
        logger.debug(f"Resampling for {v.name!r}: {method}")
        out[v.name] = method
    return out


def _per_variable[T](
    grid: GridInfo, value: T | Mapping[str, T], default: T, label: str
) -> dict[str, T]:
    """Expand a scalar or a partial mapping to one value per variable (D-57)."""
    names = [v.name for v in grid.variables]
    if not isinstance(value, Mapping):
        return dict.fromkeys(names, value)
    unknown = [k for k in value if k not in names]
    if unknown:
        raise ValueError(f"{label} has unknown variable(s) {unknown}; valid: {names}.")
    return {n: value.get(n, default) for n in names}


def _resolve_aliases(spec: Mapping[str, int], grid: GridInfo, valid_dims: list[str]) -> dict:
    """Rename the generic keys 'y'/'x' to the grid's spatial dim names (D-59)."""
    out = dict(spec)
    for alias, real in (("y", grid.y_dim), ("x", grid.x_dim)):
        if alias == real or alias not in out or alias in valid_dims:
            continue  # a real dim of that name (spatial or not) needs no aliasing
        if real in out:
            raise ValueError(f"shard_size has both {alias!r} and {real!r}; they are the same dim.")
        out[real] = out.pop(alias)
    return out


def _resolve_shard(
    spec: Any, var: VarInfo, tile: int, grid: GridInfo, valid_dims: list[str]
) -> dict[str, int] | None:
    """One shard spec (None, int or dim -> px) to a dim -> px dict for ``var`` (D-58).

    A value of -1 means the whole dim; 'y'/'x' alias the spatial dims (D-59).
    """
    if spec is None:
        return None
    if isinstance(spec, (str, bool)) or not isinstance(spec, (int, Mapping)):
        raise ValueError(
            f"shard_size {spec!r} is not a shape: shard size is now in pixels "
            "(an int, or a mapping of dim name to pixels), not bytes."
        )
    if isinstance(spec, int):
        spec = {grid.y_dim: spec, grid.x_dim: spec}
    spec = _resolve_aliases(spec, grid, valid_dims)
    unknown = [k for k in spec if k not in valid_dims]
    if unknown:
        raise ValueError(
            f"shard_size has unknown dim(s) {unknown}; valid: {valid_dims} "
            "(plus 'y'/'x' for the spatial dims)."
        )
    missing = [d for d in (grid.y_dim, grid.x_dim) if d not in spec]
    if missing:
        raise ValueError(f"shard_size needs both spatial dims; missing {missing}.")
    if any(
        isinstance(n, bool) or not isinstance(n, int) or (n < 1 and n != -1) for n in spec.values()
    ):
        raise ValueError(
            f"shard_size values must be ints >= 1 or -1 (whole dim), got {dict(spec)}."
        )
    out: dict[str, int] = {}
    for dim in var.dims:  # the variable's own dims, in array order; extra dims default to 1
        if dim not in spec:
            continue
        n = spec[dim]
        if dim in (grid.y_dim, grid.x_dim):
            if n == -1:  # whole extent, rounded up to whole chunks; clipped per level later
                n = math.ceil(var.shape[var.dims.index(dim)] / tile) * tile
        elif n == -1:  # whole extra dim
            n = var.shape[var.dims.index(dim)]
        if dim in (grid.y_dim, grid.x_dim) and n % tile:
            rounded = math.ceil(n / tile) * tile
            logger.warning(
                f"shard_size {dim}={n} for {var.name!r} is not a multiple of its tile size "
                f"({tile}); using {rounded}"
            )
            n = rounded
        out[dim] = n
    return out


def _per_variable_shards(
    grid: GridInfo, shard_size: Any, tiles: Mapping[str, int]
) -> dict[str, dict[str, int] | None]:
    names = [v.name for v in grid.variables]
    valid_dims = list(
        dict.fromkeys([*grid.extra_dims, *(d for v in grid.variables for d in v.dims)])
    )
    dim_keys = [*valid_dims, "y", "x"]  # 'y'/'x' alias the spatial dims (D-59)
    if isinstance(shard_size, Mapping) and not all(k in dim_keys for k in shard_size):
        if not all(k in names for k in shard_size):
            raise ValueError(
                f"shard_size keys {list(shard_size)} are neither all dims {dim_keys} "
                f"nor all variables {names}."
            )
        specs = {n: shard_size.get(n) for n in names}
    else:
        specs = dict.fromkeys(names, shard_size)
    return {
        v.name: _resolve_shard(specs[v.name], v, tiles[v.name], grid, valid_dims)
        for v in grid.variables
    }


def build_plan(
    grid: GridInfo,
    *,
    resampling: str | Mapping[str, str] = "auto",
    tile_size: int | Mapping[str, int] = 512,
    max_levels: int | None = None,
    shard_size: int | Mapping[str, Any] | None = None,
) -> PyramidPlan:
    tiles = _per_variable(grid, tile_size, 512, "tile_size")
    for name, t in tiles.items():
        if t < 1:
            raise ValueError(f"tile_size must be >= 1, got {t} for {name!r}")
    if max_levels is not None and max_levels < 1:
        raise ValueError(f"max_levels must be >= 1, got {max_levels}")
    shards = _per_variable_shards(grid, shard_size, tiles)
    top_tile = min(tiles.values())
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
        chunks = {}
        for v in grid.variables:
            name, shape = v.name, level.var_shape(v.name)
            if shards[name] is None:
                chunks[name] = choose_chunks(
                    shape, itemsize[name], tile_size=tiles[name], shard_size=AUTO_SHARD_BYTES
                )
            else:
                shard = tuple(shards[name].get(d, 1) for d in v.dims)
                chunks[name] = fixed_chunks(
                    shape, itemsize[name], tile_size=tiles[name], shard=shard
                )
        object.__setattr__(level, "chunks", chunks)
        levels.append(level)
        if max(ny, nx) <= top_tile or (max_levels is not None and len(levels) >= max_levels):
            break
        ny, nx = math.ceil(ny / 2), math.ceil(nx / 2)
        k += 1

    return PyramidPlan(
        grid=grid,
        resampling=methods,
        levels=tuple(levels),
        tile_size=tiles,
        shard_size=shards,
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


def _shard_text(shard: Mapping[str, int] | None) -> str:
    return "auto" if shard is None else " × ".join(f"{d} {n}" for d, n in shard.items())


def _format_plan(plan: PyramidPlan) -> str:
    grid = plan.grid
    crs = grid.crs
    epsg = crs.to_epsg()
    auth = crs.to_authority()
    crs_label = f"{auth[0]}:{auth[1]}" if auth else (f"EPSG:{epsg}" if epsg else crs.name)
    extra = ", ".join(f"{k}={v}" for k, v in grid.extra_dims.items()) or "none"
    tiles = set(plan.tile_size.values())
    shards = {_shard_text(s) for s in plan.shard_size.values()}
    uniform = len(tiles) <= 1 and len(shards) <= 1
    if uniform:
        sizes = f"tile: {tiles.pop()} px   shard: {shards.pop()}"
    else:
        sizes = "tile/shard: per variable"
    lines = [
        f"PyramidPlan: {len(plan.levels)} level(s), CRS {crs_label}",
        f"  dims: ({grid.y_dim}, {grid.x_dim})   extra dims: {extra}",
        f"  {sizes}",
        "  variables:",
    ]
    for v in grid.variables:
        line = f"    {v.name}: {plan.resampling[v.name]}, {v.dtype}"
        if not uniform:
            line += (
                f", tile {plan.tile_size[v.name]} px, shard {_shard_text(plan.shard_size[v.name])}"
            )
        lines.append(line)
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
