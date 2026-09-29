"""GeoZarr pyramid validation (PLAN 4.7): conventions, structure and (optionally) the plan."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import xarray as xr
import zarr
import zarr.abc.store
from geozarr_toolkit import validate_group
from loguru import logger

from .plan import PyramidPlan

KEYS = ("zarr_conventions", "multiscales", "spatial", "proj", "structure")

try:  # not exported at the toolkit top level
    from geozarr_toolkit.helpers.validation import validate_multiscales_structure
except ImportError:  # pragma: no cover
    validate_multiscales_structure = None  # type: ignore[assignment]


def is_valid(report: Mapping[str, list[str]]) -> bool:
    """True if a validation report has no messages."""
    return not any(report.values())


def _open_root(store: Any, storage_options: Mapping[str, Any] | None):
    from .write import _resolve_store  # local: write.py imports validate (avoid a cycle)

    zstore = _resolve_store(store, storage_options)
    try:
        root = zarr.open_group(zstore, mode="r")
    except FileNotFoundError:
        raise FileNotFoundError(f"No zarr group found at {store!r}.") from None
    except Exception as err:
        if type(err).__name__ in ("GroupNotFoundError", "NodeNotFoundError", "KeyError"):
            raise FileNotFoundError(f"No zarr group found at {store!r}.") from err
        raise FileNotFoundError(f"Cannot read zarr store {store!r}: {err}") from err
    return zstore, root


def _data_vars(group: zarr.Group, sdims: list[str]) -> dict[str, zarr.Array]:
    out = {}
    for name, arr in group.arrays():
        dn = arr.metadata.dimension_names or ()
        if sdims and all(d in dn for d in sdims):
            out[name] = arr
    return out


def _close(a: Any, b: Any, rel: float = 1e-9) -> bool:
    try:
        return len(a) == len(b) and all(
            math.isclose(float(x), float(y), rel_tol=rel, abs_tol=1e-12)
            for x, y in zip(a, b, strict=True)
        )
    except (TypeError, ValueError):
        return False


_PROJ_KEYS = ("proj:code", "proj:wkt2", "proj:projjson")


def _has_proj(attrs: Mapping[str, Any]) -> bool:
    return any(attrs.get(k) for k in _PROJ_KEYS)


def _check_required_root(report: dict[str, list[str]], attrs: Mapping[str, Any]) -> None:
    from geozarr_toolkit.conventions import MULTISCALES_UUID, PROJ_UUID, SPATIAL_UUID

    convs = attrs.get("zarr_conventions")
    declared = (
        {c.get("uuid") for c in convs if isinstance(c, dict)} if isinstance(convs, list) else set()
    )
    for key, uuid in (
        ("multiscales", MULTISCALES_UUID),
        ("spatial", SPATIAL_UUID),
        ("proj", PROJ_UUID),
    ):
        if uuid not in declared:
            report[key].append(
                f"root: {key} not declared in zarr_conventions (convention required)"
            )
    if "multiscales" not in attrs:
        report["multiscales"].append("root: no multiscales attribute (convention required)")
    if "spatial:dimensions" not in attrs:
        report["spatial"].append("root: no spatial:dimensions attribute (convention required)")
    if not _has_proj(attrs):
        report["proj"].append("root: no proj:* attribute (convention required)")


def _check_required_level(
    report: dict[str, list[str]], i: int, attrs: Mapping[str, Any], rattrs: Mapping[str, Any]
) -> None:
    for k in ("spatial:transform", "spatial:shape"):
        if k not in attrs:
            report["spatial"].append(f"level {i}: no {k} attribute (convention required)")
    if not _has_proj(attrs) and not _has_proj(rattrs):
        report["proj"].append(
            f"level {i}: no proj:* attribute on level or root (convention required)"
        )


def validate(
    store: Any,
    *,
    plan: PyramidPlan | None = None,
    storage_options: Mapping[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Validate a GeoZarr pyramid.

    Returns a report with keys ``zarr_conventions``, ``multiscales``, ``spatial``, ``proj`` and
    ``structure``; empty lists everywhere mean the pyramid is valid. Invalid content never
    raises; an unreadable or missing store raises ``FileNotFoundError``.
    """
    zstore, root = _open_root(store, storage_options)
    report: dict[str, list[str]] = {k: [] for k in KEYS}
    struct = report["structure"]

    def add_conventions(label: str, group: zarr.Group) -> None:
        # validate_group auto-detects: a convention is only checked if the attrs declare it
        # (via zarr_conventions, or via spatial:dimensions / proj:* / multiscales keys).
        for key, msgs in validate_group(group).items():
            report.setdefault(key, []).extend(f"{label}: {m}" for m in msgs)
        logger.debug("validated conventions of {}", label)

    add_conventions("root", root)
    rattrs = dict(root.attrs)
    _check_required_root(report, rattrs)

    ms = rattrs.get("multiscales")
    layout = ms.get("layout") if isinstance(ms, dict) else None
    if not layout:
        struct.append("root: 'multiscales' with a non-empty layout is required")
        layout = []
    if validate_multiscales_structure is not None and ms is not None:
        try:
            _, msgs = validate_multiscales_structure(root)
            struct.extend(f"root: {m}" for m in msgs)
        except Exception as err:  # pragma: no cover
            struct.append(f"root: validate_multiscales_structure failed: {err}")

    assets = [str(lv.get("asset")) for lv in layout if isinstance(lv, dict)]
    for i, lv in enumerate(layout):
        parent = lv.get("derived_from") if isinstance(lv, dict) else None
        if parent is not None and str(parent) not in assets[:i]:
            struct.append(
                f"level {i}: derived_from {parent!r} is not an earlier asset {assets[:i]}"
            )

    # level groups
    groups: dict[int, zarr.Group] = {}
    for i, asset in enumerate(assets):
        try:
            g = root[asset]
        except KeyError:
            struct.append(f"level {i}: asset {asset!r} does not exist")
            continue
        if not isinstance(g, zarr.Group):
            struct.append(f"level {i}: asset {asset!r} is not a group")
            continue
        groups[i] = g
        add_conventions(f"level {i}", g)
        _check_required_level(report, i, dict(g.attrs), rattrs)

    level_vars: dict[int, dict[str, zarr.Array]] = {}
    for i, g in groups.items():
        attrs = dict(g.attrs)
        sdims = attrs.get("spatial:dimensions") or rattrs.get("spatial:dimensions") or []
        vars_ = _data_vars(g, list(sdims))
        level_vars[i] = vars_
        shape = attrs.get("spatial:shape")
        if shape is not None and sdims and vars_:
            for name, arr in vars_.items():
                dn = list(arr.metadata.dimension_names)
                actual = [arr.shape[dn.index(d)] for d in sdims]
                if list(shape) != actual:
                    struct.append(
                        f"level {i}: spatial:shape {list(shape)} does not match array "
                        f"{name!r} spatial shape {actual}"
                    )
        elif shape is None:
            struct.append(f"level {i}: spatial:shape is missing")
        logger.debug("level {}: variables {}", i, sorted(vars_))

    if 0 in level_vars:
        ref = set(level_vars[0])
        for i, vars_ in level_vars.items():
            if i and set(vars_) != ref:
                struct.append(
                    f"level {i}: variables {sorted(vars_)} differ from level 0 {sorted(ref)}"
                )

    try:
        xr.open_datatree(zstore, engine="zarr", consolidated=False).close()
    except Exception as err:
        struct.append(f"xr.open_datatree failed: {type(err).__name__}: {err}")

    if plan is not None:
        _check_plan(report, plan, groups, level_vars, len(assets))

    n = sum(len(v) for v in report.values())
    if n:
        logger.info("validation found {} problem(s)", n)
    else:
        logger.info("validation passed")
    return report


def _check_plan(
    report: dict[str, list[str]],
    plan: PyramidPlan,
    groups: dict[int, zarr.Group],
    level_vars: dict[int, dict[str, zarr.Array]],
    n_levels: int,
) -> None:
    struct = report["structure"]
    if n_levels != len(plan.levels):
        struct.append(f"plan: expected {len(plan.levels)} level(s), found {n_levels}")
    expected_vars = {v.name for v in plan.grid.variables}
    for k, lp in enumerate(plan.levels):
        if k not in groups:
            continue
        attrs = dict(groups[k].attrs)
        shape = attrs.get("spatial:shape")
        if shape is not None and tuple(shape) != tuple(lp.shape):
            struct.append(f"plan: level {k} shape {tuple(shape)} != planned {tuple(lp.shape)}")
        tr = attrs.get("spatial:transform")
        if tr is None:
            struct.append(f"plan: level {k} has no spatial:transform")
        elif not _close(tr, lp.transform):
            struct.append(
                f"plan: level {k} spatial:transform {list(tr)} != planned {list(lp.transform)}"
            )
        vars_ = level_vars.get(k, {})
        if set(vars_) != expected_vars:
            struct.append(
                f"plan: level {k} variables {sorted(vars_)} != planned {sorted(expected_vars)}"
            )
        for name, arr in vars_.items():
            spec = lp.chunks.get(name)
            if spec is None:
                continue
            if tuple(arr.chunks) != tuple(spec.chunks):
                struct.append(
                    f"plan: level {k} {name!r} chunks {tuple(arr.chunks)} "
                    f"!= planned {tuple(spec.chunks)}"
                )
            shards = tuple(arr.shards) if arr.shards is not None else None
            planned = tuple(spec.shards) if spec.shards is not None else None
            if shards != planned:
                struct.append(f"plan: level {k} {name!r} shards {shards} != planned {planned}")
