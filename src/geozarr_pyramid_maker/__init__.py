"""Convert regular-grid xarray data into GeoZarr multiscale pyramids."""

from importlib.metadata import PackageNotFoundError, version

from ._logging import configure_logging

try:
    __version__ = version("geozarr-pyramid-maker")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0+unknown"

__all__ = ["__version__", "configure_logging"]
