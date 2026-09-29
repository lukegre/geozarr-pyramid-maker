"""The ``.geozarr`` xarray accessor (D-10). Kept thin: the logic lives in detect/plan/write."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import xarray as xr
from loguru import logger

from .detect import detect
from .plan import PyramidPlan, build_plan
from .write import PyramidResult
from .write import to_pyramid as _to_pyramid


class _GeoZarrAccessor:
    def __init__(self, obj: xr.Dataset | xr.DataArray) -> None:
        self._obj = obj

    def plan(
        self,
        *,
        crs: Any = None,
        resampling: str | Mapping[str, str] = "auto",
        tile_size: int = 512,
        max_levels: int | None = None,
        shard_size: int | str = "128MiB",
    ) -> PyramidPlan:
        """Dry run: detect the grid and return the pyramid plan without touching any data."""
        _, grid = detect(self._obj, crs=crs)
        plan = build_plan(
            grid,
            resampling=resampling,
            tile_size=tile_size,
            max_levels=max_levels,
            shard_size=shard_size,
        )
        logger.debug("Pyramid plan:\n{}", plan)
        return plan

    def to_pyramid(self, store: Any, **kwargs: Any) -> PyramidResult:
        """Write a GeoZarr multiscale pyramid (see ``write.to_pyramid``)."""
        return _to_pyramid(self._obj, store, **kwargs)


@xr.register_dataset_accessor("geozarr")
class GeoZarrDatasetAccessor(_GeoZarrAccessor):
    """``ds.geozarr`` accessor."""


@xr.register_dataarray_accessor("geozarr")
class GeoZarrDataArrayAccessor(_GeoZarrAccessor):
    """``da.geozarr`` accessor."""
