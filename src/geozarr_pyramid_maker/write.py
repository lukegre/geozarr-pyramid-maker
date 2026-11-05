"""Pyramid writer (PLAN 4.6): levels, GeoZarr metadata, consolidation and validation."""

from __future__ import annotations

import os
import re
import time
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
import zarr
import zarr.abc.store
import zarr.storage
from loguru import logger
from numcodecs import BitRound

from .chunking import ChunkSpec
from .detect import detect
from .metadata import cf_prepare, level_attrs, root_attrs
from .plan import PyramidPlan, build_plan
from .resample import downsample

_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_OBSTORE_SCHEMES = ("s3", "gs", "gcs", "az", "abfs", "memory")
_CLOUD_HINT = "pip install geozarr-pyramid-maker[cloud]"


@dataclass(frozen=True)
class PyramidResult:
    store: str | zarr.abc.store.Store
    plan: PyramidPlan
    validation: dict[str, list[str]] | None  # None when validate=False
    timings: Mapping[str, float]  # "level_0": s, ..., "total": s
    preview_path: Path | None = None  # set when preview=True


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
            logger.debug("obstore is not installed; falling back to fsspec for {}", scheme)
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


def _s3_endpoint_options(endpoint: str) -> dict[str, Any]:
    """storage_options for a custom S3 endpoint, for the backend _resolve_store picks.

    Reads are unsigned when no AWS credentials are configured in the environment; otherwise
    obstore would probe the instance-metadata service and fail.
    """
    anonymous = not any(
        os.environ.get(k)
        for k in ("AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_WEB_IDENTITY_TOKEN_FILE")
    )
    try:
        import obstore.store  # noqa: F401
    except ImportError:
        opts: dict[str, Any] = {"endpoint_url": endpoint}  # s3fs / fsspec
        if anonymous:
            opts["anon"] = True
        return opts
    opts = {"endpoint": endpoint}
    if endpoint.lower().startswith("http://"):
        # allow_http is an obstore client option, not an aws_-prefixed S3 config key
        opts["client_options"] = {"allow_http": True}
    if anonymous:
        opts["skip_signature"] = True
    return opts


def _norm_group(group: str | None) -> str:
    """Strip surrounding slashes; "" means the store root."""
    g = (group or "").strip("/")
    if "//" in g or any(part in (".", "..") for part in g.split("/")):
        raise ValueError(f"invalid group path {group!r}")
    return g


def _join(group: str, name: str) -> str:
    return f"{group}/{name}" if group else name


def _store_has_data(store: zarr.abc.store.Store, group: str = "") -> bool:
    try:
        zarr.open(store, path=group or None, mode="r")
    except (FileNotFoundError, KeyError, ValueError):
        return False
    except Exception as err:  # zarr's NodeNotFoundError variants
        if type(err).__name__ in ("GroupNotFoundError", "ArrayNotFoundError", "NodeNotFoundError"):
            return False
        raise
    return True


def _check_local_target(
    zstore: zarr.abc.store.Store, store: Any, overwrite: bool, group: str = ""
) -> None:
    """Never delete or write into a non-empty local directory that is not a Zarr store."""
    if not isinstance(zstore, zarr.storage.LocalStore):
        return
    root = Path(zstore.root)
    if root.is_dir() and any(root.iterdir()) and not (root / "zarr.json").exists():
        action = "delete" if overwrite and not group else "write into"
        raise FileExistsError(f"{store!r} is not a Zarr store; refusing to {action} it")


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


def _write_level(
    ds, store, plan: PyramidPlan, k: int, compression_level: int, group: str = ""
) -> None:
    level = plan.levels[k]
    fills = {v.name: v.fill_value for v in plan.grid.variables}
    ds = _prepare(ds, level.chunks, fills)
    g = plan.grid
    if k > 0:
        # belt and braces: the resampled coords must match the plan's transform
        ny, nx = level.shape
        a, _, c, _, e, f = level.transform
        for dim, n, origin, step in ((g.x_dim, nx, c, a), (g.y_dim, ny, f, e)):
            expected = origin + step * (np.arange(n) + 0.5)
            if not np.allclose(ds[dim].values, expected, rtol=0, atol=abs(step) * 1e-6):
                raise RuntimeError(
                    f"Level {k} coordinate {dim!r} does not match the plan transform"
                )
    ds = cf_prepare(ds, g.crs, level.transform, g.x_dim, g.y_dim, plan.resampling)
    enc = _encoding(ds, level.chunks, fills, compression_level)
    path = _join(group, str(k))
    ds.to_zarr(
        store,
        group=path,
        zarr_format=3,
        mode="w",
        consolidated=False,
        write_empty_chunks=False,
        encoding=enc,
    )
    zarr.open_group(store, path=path, mode="r+").attrs.update(level_attrs(plan, k))
    for v in plan.grid.variables:
        shape = zarr.open_array(store, path=f"{path}/{v.name}", mode="r").shape
        if shape != level.var_shape(v.name):
            raise RuntimeError(
                f"Level {k} variable {v.name!r} has shape {shape}, plan says "
                f"{level.var_shape(v.name)}"
            )


# --------------------------------------------------------------------------- bit rounding


def _resolve_keepbits(grid, keepbits: int | Mapping[str, int] | None) -> dict[str, int]:
    """Per-variable keepbits for the float variables (D-60); raises before anything is written."""
    if keepbits is None:
        return {}
    floats = {v.name: v.dtype for v in grid.variables if v.dtype in (np.float32, np.float64)}
    if isinstance(keepbits, Mapping):
        bad = [k for k in keepbits if k not in floats]
        if bad:
            raise ValueError(
                f"keepbits names unknown or non-float variable(s) {bad}; float variables: "
                f"{list(floats)}."
            )
        requested = dict(keepbits)
    else:
        requested = dict.fromkeys(floats, keepbits)
        for v in grid.variables:
            if v.name not in floats:
                logger.debug("keepbits: skipping non-float variable {!r} ({})", v.name, v.dtype)
    for name, n in requested.items():
        top = 23 if floats[name] == np.float32 else 52
        if isinstance(n, bool) or not isinstance(n, (int, np.integer)) or not 0 <= n <= top:
            raise ValueError(
                f"keepbits for {name!r} must be an integer in 0-{top} ({floats[name]}), got {n!r}."
            )
    return {k: int(n) for k, n in requested.items()}


def _bitround(da: xr.DataArray, keepbits: int, fill: Any) -> xr.DataArray:
    """Round-to-nearest to ``keepbits`` mantissa bits; NaN and the fill value pass through."""
    codec = BitRound(keepbits=keepbits)
    ftype = da.dtype

    def _round(a):
        a = np.ascontiguousarray(a, dtype=ftype)
        r = codec.encode(a).view(ftype).reshape(a.shape)  # encode returns the bit pattern
        keep = np.isnan(a) if fill is None or np.isnan(fill) else (np.isnan(a) | (a == fill))
        return np.where(keep, a, r)

    out = xr.apply_ufunc(_round, da, dask="parallelized", output_dtypes=[ftype], keep_attrs=True)
    out.attrs["bitround_keepbits"] = keepbits
    return out


def _bitround_level(
    ds: xr.Dataset, keepbits: Mapping[str, int], fills: Mapping[str, Any]
) -> xr.Dataset:
    if not keepbits:
        return ds
    return ds.assign({n: _bitround(ds[n], k, fills.get(n)) for n, k in keepbits.items()})


def _fmt_size(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    raise AssertionError  # pragma: no cover


# --------------------------------------------------------------------------- public


def to_pyramid(
    obj: xr.Dataset | xr.DataArray,
    store: str | Path | zarr.abc.store.Store,
    *,
    group: str | None = None,
    crs: Any = None,
    fill_values: Mapping[str, int | float] | None = None,
    resampling: str | Mapping[str, str] = "auto",
    tile_size: int | Mapping[str, int] = 512,
    max_levels: int | None = None,
    shard_size: int | Mapping[str, Any] | None = None,
    keepbits: int | Mapping[str, int] | None = None,
    compression_level: int = 3,
    overwrite: bool = False,
    validate: bool = True,
    preview: bool = False,
    storage_options: Mapping[str, Any] | None = None,
) -> PyramidResult:
    """Write a GeoZarr multiscale pyramid (data only until M3)."""
    t_start = time.perf_counter()
    grp = _norm_group(group)  # D-62: "" = the store root
    ds, grid = detect(obj, crs=crs, fill_values=fill_values)
    ds_attrs = dict(ds.attrs)
    plan = build_plan(
        grid,
        resampling=resampling,
        tile_size=tile_size,
        max_levels=max_levels,
        shard_size=shard_size,
    )
    plan = replace(plan, keepbits=_resolve_keepbits(grid, keepbits))
    total = sum(lv.nbytes for lv in plan.levels)
    logger.info(
        "Pyramid plan: {} level(s), {} uncompressed in total", len(plan.levels), _fmt_size(total)
    )
    logger.debug("Pyramid plan:\n{}", plan)

    zstore = _resolve_store(store, storage_options)
    where = f"{store!r} group {grp!r}" if grp else f"{store!r}"
    _check_local_target(zstore, store, overwrite, grp)
    if _store_has_data(zstore, grp):
        if not overwrite:
            raise FileExistsError(
                f"{where} already contains a zarr group or array; pass overwrite=True to replace it"
            )
        logger.warning("Overwriting existing zarr data in {}", where)
        # with a group, only that sub-tree is deleted and recreated; the root is never touched
        zarr.open_group(zstore, path=grp or None, mode="w")

    if not any(v.chunks is not None for v in ds.data_vars.values()):
        logger.debug("Input is not dask-backed; chunking it to the level-0 plan")

    timings: dict[str, float] = {}
    variables = [v.name for v in grid.variables]
    fills = {v.name: v.fill_value for v in grid.variables}

    t0 = time.perf_counter()
    _write_level(_bitround_level(ds, plan.keepbits, fills), zstore, plan, 0, compression_level, grp)
    timings["level_0"] = time.perf_counter() - t0
    logger.info(
        "Level 0 written: shape={} in {:.1f} s ({} uncompressed)",
        plan.levels[0].shape,
        timings["level_0"],
        _fmt_size(plan.levels[0].nbytes),
    )

    for k in range(len(plan.levels) - 1):
        t0 = time.perf_counter()
        # D-05: re-read level k lazily. chunks=None keeps the backend arrays lazy; we then chunk
        # each variable to its shard shape (open_zarr(chunks={}) would give the *inner* chunks
        # for sharded arrays, i.e. many tiny tasks). mask_and_scale=False keeps ints raw.
        src = xr.open_zarr(
            zstore, group=_join(grp, str(k)), consolidated=False, chunks=None, mask_and_scale=False
        )
        src = src[variables].drop_vars("spatial_ref", errors="ignore")  # rebuilt per level
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
            steps=(plan.levels[k].transform[4], plan.levels[k].transform[0]),
        )
        coarse = _bitround_level(coarse, plan.keepbits, fills)
        _write_level(coarse, zstore, plan, k + 1, compression_level, grp)
        timings[f"level_{k + 1}"] = time.perf_counter() - t0
        logger.info(
            "Level {} written: shape={} in {:.1f} s ({} uncompressed)",
            k + 1,
            plan.levels[k + 1].shape,
            timings[f"level_{k + 1}"],
            _fmt_size(plan.levels[k + 1].nbytes),
        )

    # root attrs go on the pyramid group (the store root itself when no group is given)
    zarr.open_group(zstore, path=grp or None, mode="r+").attrs.update(
        root_attrs(plan, ds_attrs)
    )  # before D-19

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Consolidated metadata is currently not part", category=UserWarning
        )
        zarr.consolidate_metadata(zstore, path=grp or None)
        if grp:  # D-62: refresh the root's inline metadata so it lists the new group
            try:
                zarr.open_group(zstore, mode="r")
            except Exception:
                logger.warning(
                    "Store root of {!r} is not a zarr group; its metadata was not consolidated",
                    store,
                )
            else:
                zarr.consolidate_metadata(zstore)
    report = None
    if validate:
        from .validate import validate as _validate

        report = _validate(zstore, group=grp or None, plan=plan, storage_options=storage_options)
        errors = [f"{k}: {e}" for k, errs in report.items() for e in errs]
        if errors:
            # validate() already logged the one-line result; add the details here
            logger.error("Validation problems:\n{}", "\n".join(errors))
    timings["total"] = time.perf_counter() - t_start
    result_store = (
        store if isinstance(store, str) else (str(store) if isinstance(store, Path) else zstore)
    )
    preview_path = None
    if preview:
        if isinstance(result_store, (str, Path)):
            from .preview import preview as _preview

            preview_path = _preview(result_store)
        else:
            logger.warning("preview=True needs a path or URL store; skipping the preview page")
    logger.info("Pyramid written to {} in {:.1f} s", result_store, timings["total"])
    return PyramidResult(
        store=result_store,
        plan=plan,
        validation=report,
        timings=timings,
        preview_path=preview_path,
    )
