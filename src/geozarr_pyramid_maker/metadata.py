"""GeoZarr metadata (PLAN 4.5): spatial/proj/multiscales attrs and CF grid mapping.

Everything convention-related is built with ``geozarr-toolkit`` helpers and models.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pyproj
import rioxarray  # noqa: F401  (registers the .rio accessor)
import xarray as xr
from affine import Affine
from geozarr_toolkit import (
    MultiscalesConventionMetadata,
    ProjConventionMetadata,
    SpatialConventionMetadata,
    create_multiscales_layout,
    create_proj_attrs,
    create_spatial_attrs,
    create_zarr_conventions,
)

from .plan import PyramidPlan
from .resample import metadata_method_name

# D-16: geozarr-toolkit 0.1.2 embeds schema URLs (refs/tags/v1) that return 404. We write the live
# upstream refs/tags/v0.1 URLs instead (zarr-conventions/proj for proj:). The UUIDs stay as shipped.
MULTISCALES_SCHEMA_URL = (
    "https://raw.githubusercontent.com/zarr-conventions/multiscales/refs/tags/v0.1/schema.json"
)
SPATIAL_SCHEMA_URL = (
    "https://raw.githubusercontent.com/zarr-conventions/spatial/refs/tags/v0.1/schema.json"
)
PROJ_SCHEMA_URL = (
    "https://raw.githubusercontent.com/zarr-conventions/proj/refs/tags/v0.1/schema.json"
)

# spec_url overrides too: the toolkit points at .../blob/v1/README.md (proj: zarr-experimental).
MULTISCALES_SPEC_URL = "https://github.com/zarr-conventions/multiscales/blob/v0.1/README.md"
SPATIAL_SPEC_URL = "https://github.com/zarr-conventions/spatial/blob/v0.1/README.md"
PROJ_SPEC_URL = "https://github.com/zarr-conventions/proj/blob/v0.1/README.md"

GRID_MAPPING_NAME = "spatial_ref"
_CODE_RE = re.compile(r"^[A-Z]+:[0-9]+$")
_MANAGED_PREFIXES = ("spatial:", "proj:")
VARIABLES_ATTR = "geozarr_pyramid_maker:variables"  # source variable order (zarr sorts arrays)
_MANAGED_KEYS = ("multiscales", "zarr_conventions", VARIABLES_ATTR)


def convention_metadata() -> tuple[
    MultiscalesConventionMetadata, SpatialConventionMetadata, ProjConventionMetadata
]:
    """The three convention registrations with the D-16 ``schema_url``/``spec_url`` overrides."""
    return (
        MultiscalesConventionMetadata(
            schema_url=MULTISCALES_SCHEMA_URL, spec_url=MULTISCALES_SPEC_URL
        ),
        SpatialConventionMetadata(schema_url=SPATIAL_SCHEMA_URL, spec_url=SPATIAL_SPEC_URL),
        ProjConventionMetadata(schema_url=PROJ_SCHEMA_URL, spec_url=PROJ_SPEC_URL),
    )


def _conventions(*names: str) -> list[dict[str, Any]]:
    multiscales, spatial, proj = convention_metadata()
    chosen = {"multiscales": multiscales, "spatial": spatial, "proj": proj}
    return create_zarr_conventions(*(chosen[n] for n in names))


def proj_attrs(crs: pyproj.CRS) -> dict[str, Any]:
    """``proj:code`` (when the CRS has a usable authority code) and always ``proj:wkt2``."""
    code = None
    auth = crs.to_authority()
    if auth is not None:
        candidate = f"{auth[0]}:{auth[1]}"
        if _CODE_RE.match(candidate):
            code = candidate
    return create_proj_attrs(code=code, wkt2=crs.to_wkt())


def _spatial_attrs(plan: PyramidPlan, k: int) -> dict[str, Any]:
    level = plan.levels[k]
    grid = plan.grid
    return create_spatial_attrs(
        [grid.y_dim, grid.x_dim],
        transform=list(level.transform),
        bbox=list(level.bbox),
        shape=list(level.shape),
        registration="pixel",
    )


def level_attrs(plan: PyramidPlan, level: int) -> dict[str, Any]:
    """Group attrs for level ``level``: spatial + proj + their convention registrations."""
    attrs = _spatial_attrs(plan, level)
    attrs.update(proj_attrs(plan.grid.crs))
    attrs["zarr_conventions"] = _conventions("spatial", "proj")
    return attrs


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _history(source: Mapping[str, Any]) -> str:
    from . import __version__

    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    line = f"{stamp}: created by geozarr-pyramid-maker v{__version__}"
    old = source.get("history")
    return f"{old}\n{line}" if old else line


def root_attrs(plan: PyramidPlan, source_attrs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Root group attrs: source globals, history, multiscales layout, spatial and proj."""
    source = dict(source_attrs or {})
    attrs: dict[str, Any] = {
        k: _jsonable(v)
        for k, v in source.items()
        if k not in _MANAGED_KEYS and not str(k).startswith(_MANAGED_PREFIXES)
    }
    attrs["history"] = _history(source)

    levels: list[dict[str, Any]] = []
    for k in range(len(plan.levels)):
        entry: dict[str, Any] = {"asset": str(k)}
        if k > 0:
            entry["derived_from"] = str(k - 1)
            entry["transform"] = {"scale": [2.0, 2.0], "translation": [0.0, 0.0]}
        levels.append(entry)
    methods = {metadata_method_name(m) for m in plan.resampling.values()}
    layout = create_multiscales_layout(
        levels, resampling_method=methods.pop() if len(methods) == 1 else None
    )
    # the toolkit helper drops extra per-level keys; the model allows them, so add them here
    entries = [dict(e) for e in layout["multiscales"]["layout"]]
    for k, entry in enumerate(entries):
        lv = plan.levels[k]
        entry["spatial:shape"] = list(lv.shape)
        entry["spatial:transform"] = list(lv.transform)
    layout["multiscales"]["layout"] = entries
    attrs.update(layout)

    spatial = _spatial_attrs(plan, 0)
    spatial.pop("spatial:transform", None)  # levels carry their own transform
    spatial.pop("spatial:shape", None)
    attrs.update(spatial)
    attrs.update(proj_attrs(plan.grid.crs))
    attrs["zarr_conventions"] = _conventions("multiscales", "spatial", "proj")
    attrs[VARIABLES_ATTR] = [v.name for v in plan.grid.variables]
    return attrs


def cf_prepare(
    ds: xr.Dataset,
    crs: pyproj.CRS,
    transform: tuple[float, ...],
    x_dim: str,
    y_dim: str,
    resampling: Mapping[str, str] | None = None,
) -> xr.Dataset:
    """Add CF/GDAL georeferencing: ``spatial_ref``, x/y axis attrs and ``grid_mapping``."""
    ds = ds.drop_vars(GRID_MAPPING_NAME, errors="ignore")
    ds = ds.rio.set_spatial_dims(x_dim=x_dim, y_dim=y_dim, inplace=False)
    ds = ds.rio.write_crs(crs, grid_mapping_name=GRID_MAPPING_NAME)
    ds = ds.rio.write_transform(Affine(*transform), grid_mapping_name=GRID_MAPPING_NAME)
    ds = ds.rio.write_coordinate_system()
    for name in ds.data_vars:
        v = ds[name]
        v.attrs["grid_mapping"] = GRID_MAPPING_NAME
        v.encoding.pop("grid_mapping", None)
        if resampling is not None and str(name) in resampling:
            v.attrs["resampling_method"] = metadata_method_name(resampling[str(name)])
    return ds
