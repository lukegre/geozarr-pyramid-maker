"""Pyramid writer (PLAN 4.6): data only, metadata lands in M3."""

from __future__ import annotations

import re
import time
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import xarray as xr
import zarr
import zarr.abc.store
import zarr.storage
from loguru import logger

from .chunking import ChunkSpec
from .detect import detect
from .plan import PyramidPlan, build_plan
from .resample import downsample

_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_OBSTORE_SCHEMES = ("s3", "gs", "gcs", "az", "abfs", "memory")
_CLOUD_HINT = "pip install geozarr-pyramid-maker[cloud]"


@dataclass(frozen=True)
class PyramidResult:
    store: str | zarr.abc.store.Store
    plan: PyramidPlan
    validation: dict[str, list[str]] | None  # None until M3
    timings: Mapping[str, float]  # "level_0": s, ..., "total": s


# --------------------------------------------------------------------------- stores


def _resolve_store(store: Any, storage_options: Mapping[str, Any] | None = None):
    """Turn a path, URL or Store into a writable zarr Store (D-13)."""
    if isinstance(store, zarr.abc.store.Store):
        return store
    if isinstance(store, Path):
        return zarr.storage.LocalStore(store)
    if not isinstance(store, str):
        raise TypeError(f"store must be a path, URL or zarr Store, got {type(store).__name__}")
    if not _URL_RE.match(store):
        return zarr.storage.LocalStore(store)

    scheme = store.split("://", 1)[0].lower()
    if scheme in _OBSTORE_SCHEMES:
        try:
            import obstore.store
        except ImportError:
            logger.debug("obstore not installed; falling back to fsspec for {}", scheme)
        else:
            logger.debug("Using obstore for {}", store)
            return zarr.storage.ObjectStore(
                obstore.store.from_url(store, **(storage_options or {})), read_only=False
            )
    try:
        import fsspec  # noqa: F401
    except ImportError as err:
        raise ImportError(
            f"Neither obstore nor fsspec is installed, so {store!r} cannot be opened. "
            f"Try: {_CLOUD_HINT}"
        ) from err
    logger.debug("Using fsspec for {}", store)
    return zarr.storage.FsspecStore.from_url(
        store, storage_options=dict(storage_options or {}), read_only=False
    )


def _store_has_data(store: zarr.abc.store.Store) -> bool:
    try:
        zarr.open(store, mode="r")
    except (FileNotFoundError, KeyError, ValueError):
        return False
    except Exception as err:  # zarr's NodeNotFoundError variants
        if type(err).__name__ in ("GroupNotFoundError", "ArrayNotFoundError", "NodeNotFoundError"):
            return False
        raise
    return True


# --------------------------------------------------------------------------- level writing


def _encoding(
    ds: xr.Dataset, specs: Mapping[str, ChunkSpec], fills: Mapping[str, Any], compression_level: int
):
    enc: dict[str, dict[str, Any]] = {}
    for name in ds.data_vars:
        name = str(name)
        spec = specs[name]
        if len(spec.chunks) != ds[name].ndim:
            raise ValueError(f"chunk spec {spec.chunks} does not match dims of {name!r}")
        e: dict[str, Any] = {
            "chunks": spec.chunks,
            "compressors": [zarr.codecs.ZstdCodec(level=compression_level)],
        }
        if spec.shards is not None:
            e["shards"] = spec.shards
        # zarr v3 in xarray 2026.7: "fill_value" is the encoding key (xarray maps a CF
        # "_FillValue" onto it as well); we set it directly and never leave _FillValue in attrs.
        fill = fills.get(name)
        if fill is not None:
            e["fill_value"] = fill
        enc[name] = e
    for name, c in ds.coords.items():
        if c.ndim >= 1:  # coords: one chunk, unsharded (xarray still gives floats a NaN fill)
            enc[str(name)] = {"chunks": tuple(c.shape)}
    return enc


def _prepare(ds: xr.Dataset, specs: Mapping[str, ChunkSpec], fills: Mapping[str, Any]):
    """Rechunk data vars to the dask chunks of their spec; strip encoding and stale attrs."""
    ds = ds.assign_coords({n: c.compute() for n, c in ds.coords.items()})
    out = {}
    for name in ds.data_vars:
        v = ds[name]
        v = v.chunk(dict(zip(v.dims, specs[str(name)].dask_chunks, strict=True)))
        v.attrs = {k: a for k, a in v.attrs.items() if k != "_FillValue"}
        out[str(name)] = v
    ds = ds.assign(out)
    ds.attrs = {}
    for v in ds.variables.values():
        v.encoding = {}
    return ds


def _write_level(ds, store, plan: PyramidPlan, k: int, compression_level: int) -> None:
    level = plan.levels[k]
    fills = {v.name: v.fill_value for v in plan.grid.variables}
    ds = _prepare(ds, level.chunks, fills)
    enc = _encoding(ds, level.chunks, fills, compression_level)
    ds.to_zarr(
        store,
        group=str(k),
        zarr_format=3,
        mode="w",
        consolidated=False,
        write_empty_chunks=False,
        encoding=enc,
    )
    for v in plan.grid.variables:
        shape = zarr.open_array(store, path=f"{k}/{v.name}", mode="r").shape
        if shape != level.var_shape(v.name):
            raise RuntimeError(
                f"Level {k} variable {v.name!r} has shape {shape}, plan says "
                f"{level.var_shape(v.name)}"
            )


def _fmt_mib(n: int) -> str:
    return f"{n / 1024**2:.1f} MiB"


# --------------------------------------------------------------------------- public


def to_pyramid(
    obj: xr.Dataset | xr.DataArray,
    store: str | Path | zarr.abc.store.Store,
    *,
    crs: Any = None,
    resampling: str | Mapping[str, str] = "auto",
    tile_size: int = 512,
    max_levels: int | None = None,
    shard_size: int | str = "128MiB",
    compression_level: int = 3,
    overwrite: bool = False,
    validate: bool = True,
    preview: bool = False,
    storage_options: Mapping[str, Any] | None = None,
) -> PyramidResult:
    """Write a GeoZarr multiscale pyramid (data only until M3)."""
    t_start = time.perf_counter()
    ds, grid = detect(obj, crs=crs)
    plan = build_plan(
        grid,
        resampling=resampling,
        tile_size=tile_size,
        max_levels=max_levels,
        shard_size=shard_size,
    )
    total = sum(lv.nbytes for lv in plan.levels)
    logger.info(
        "Pyramid plan: {} level(s), {} uncompressed in total", len(plan.levels), _fmt_mib(total)
    )
    logger.debug("Pyramid plan:\n{}", plan)

    zstore = _resolve_store(store, storage_options)
    if _store_has_data(zstore):
        if not overwrite:
            raise FileExistsError(
                f"{store!r} already contains a zarr group or array; "
                "pass overwrite=True to replace it."
            )
        logger.warning("Overwriting existing zarr data in {!r}", store)
        zarr.open_group(zstore, mode="w")

    if not any(v.chunks is not None for v in ds.data_vars.values()):
        logger.info("Input is not dask-backed; chunking it to the level-0 plan.")

    if validate:
        logger.debug("validation lands in M3")
    if preview:
        logger.warning("preview lands in M5; ignoring preview=True")

    timings: dict[str, float] = {}
    variables = [v.name for v in grid.variables]
    fills = {v.name: v.fill_value for v in grid.variables}

    t0 = time.perf_counter()
    _write_level(ds, zstore, plan, 0, compression_level)
    timings["level_0"] = time.perf_counter() - t0
    logger.info(
        "Level 0 written: shape={} in {:.1f}s ({})",
        plan.levels[0].shape,
        timings["level_0"],
        _fmt_mib(plan.levels[0].nbytes),
    )

    for k in range(len(plan.levels) - 1):
        t0 = time.perf_counter()
        # D-05: re-read level k lazily. chunks=None keeps the backend arrays lazy; we then chunk
        # each variable to its shard shape (open_zarr(chunks={}) would give the *inner* chunks
        # for sharded arrays, i.e. many tiny tasks). mask_and_scale=False keeps ints raw.
        src = xr.open_zarr(
            zstore, group=str(k), consolidated=False, chunks=None, mask_and_scale=False
        )
        src = src[variables]
        specs = plan.levels[k].chunks
        src = src.assign(
            {
                n: src[n].chunk(dict(zip(src[n].dims, specs[n].dask_chunks, strict=True)))
                for n in variables
            }
        )
        coarse = downsample(
            src,
            x_dim=grid.x_dim,
            y_dim=grid.y_dim,
            methods=plan.resampling,
            fill_values=fills,
        )
        _write_level(coarse, zstore, plan, k + 1, compression_level)
        timings[f"level_{k + 1}"] = time.perf_counter() - t0
        logger.info(
            "Level {} written: shape={} in {:.1f}s ({})",
            k + 1,
            plan.levels[k + 1].shape,
            timings[f"level_{k + 1}"],
            _fmt_mib(plan.levels[k + 1].nbytes),
        )

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Consolidated metadata is currently not part", category=UserWarning
        )
        zarr.consolidate_metadata(zstore)
    timings["total"] = time.perf_counter() - t_start
    logger.info("Pyramid written in {:.1f}s", timings["total"])
    return PyramidResult(
        store=store
        if isinstance(store, str)
        else (str(store) if isinstance(store, Path) else zstore),
        plan=plan,
        validation=None,
        timings=timings,
    )
