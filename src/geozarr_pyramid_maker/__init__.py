"""Convert regular-grid xarray data into GeoZarr multiscale pyramids."""

from importlib.metadata import PackageNotFoundError, version

from . import accessor as _accessor  # noqa: F401  (registers the .geozarr accessor)
from ._logging import configure_logging
from .detect import DetectionError, detect
from .plan import LevelPlan, PyramidPlan, build_plan
from .preview import preview
from .validate import is_valid, validate
from .write import PyramidResult

try:
    __version__ = version("geozarr-pyramid-maker")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0+unknown"

__all__ = [
    "DetectionError",
    "LevelPlan",
    "PyramidPlan",
    "PyramidResult",
    "__version__",
    "build_plan",
    "configure_logging",
    "detect",
    "is_valid",
    "preview",
    "validate",
]
