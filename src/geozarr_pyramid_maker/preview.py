"""OpenLayers preview page and a local CORS/Range server (PLAN 4.7)."""

from __future__ import annotations

import functools
import hashlib
import html
import json
import math
import os
import posixpath
import re
import warnings
import webbrowser
from collections.abc import Callable
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import numpy as np
import pyproj
import xarray as xr
import zarr
from loguru import logger

from .metadata import VARIABLES_ATTR

OL_VERSION = "10.10.0"
PROJ4_VERSION = "2.22.0"
DEMO_STORE = "s3://spi-greenfjord-public/test/mur_sst_subset.zarr"
DEMO_ENDPOINT = "https://os.zhdk.cloud.switch.ch"
SYNTHETIC_CRS = "GPM:custom"
_BUILTIN_CRS = ("EPSG:4326", "EPSG:3857")
_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_MAX_CATEGORIES = 20
_MAX_LABELS = 5000
RAMP = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]]  # viridis
PALETTE = [
    [31, 119, 180], [255, 127, 14], [44, 160, 44], [214, 39, 40], [148, 103, 189],
    [140, 86, 75], [227, 119, 194], [127, 127, 127], [188, 189, 34], [23, 190, 207],
]  # fmt: skip

# 11 RGB stops per colourmap, sampled from matplotlib at 0, 0.1 ... 1 (no runtime dependency).
COLORMAPS: dict[str, list[list[int]]] = {
    "viridis": [
        [68, 1, 84],
        [72, 36, 117],
        [65, 68, 135],
        [53, 95, 141],
        [42, 120, 142],
        [33, 145, 140],
        [34, 168, 132],
        [68, 191, 112],
        [122, 209, 81],
        [189, 223, 38],
        [253, 231, 37],
    ],
    "magma": [
        [0, 0, 4],
        [20, 14, 54],
        [59, 15, 112],
        [100, 26, 128],
        [140, 41, 129],
        [183, 55, 121],
        [222, 73, 104],
        [247, 112, 92],
        [254, 159, 109],
        [254, 207, 146],
        [252, 253, 191],
    ],
    "inferno": [
        [0, 0, 4],
        [22, 11, 57],
        [66, 10, 104],
        [106, 23, 110],
        [147, 38, 103],
        [188, 55, 84],
        [221, 81, 58],
        [243, 120, 25],
        [252, 165, 10],
        [246, 215, 70],
        [252, 255, 164],
    ],
    "cividis": [
        [0, 34, 78],
        [8, 51, 112],
        [53, 69, 108],
        [79, 87, 108],
        [102, 105, 112],
        [125, 124, 120],
        [148, 142, 119],
        [174, 163, 113],
        [200, 184, 102],
        [229, 207, 82],
        [254, 232, 56],
    ],
    "turbo": [
        [48, 18, 59],
        [69, 89, 203],
        [62, 155, 254],
        [25, 213, 205],
        [70, 248, 132],
        [164, 252, 60],
        [225, 221, 55],
        [254, 164, 49],
        [240, 91, 18],
        [195, 37, 3],
        [122, 4, 3],
    ],
    "RdBu_r": [
        [5, 48, 97],
        [32, 101, 171],
        [67, 147, 195],
        [144, 196, 221],
        [209, 229, 240],
        [247, 246, 246],
        [253, 219, 199],
        [243, 164, 129],
        [214, 96, 77],
        [177, 24, 43],
        [103, 0, 31],
    ],
    "coolwarm": [
        [59, 76, 192],
        [89, 119, 227],
        [123, 159, 249],
        [158, 190, 255],
        [192, 212, 245],
        [221, 220, 220],
        [242, 203, 183],
        [247, 172, 142],
        [238, 132, 104],
        [214, 82, 68],
        [180, 4, 38],
    ],
    "BrBG": [
        [84, 48, 5],
        [139, 80, 10],
        [191, 129, 45],
        [222, 193, 123],
        [246, 232, 195],
        [244, 245, 245],
        [199, 234, 229],
        [127, 204, 192],
        [53, 151, 143],
        [1, 101, 93],
        [0, 60, 48],
    ],
    "PuOr": [
        [127, 59, 8],
        [178, 87, 6],
        [224, 130, 20],
        [252, 183, 97],
        [254, 224, 182],
        [246, 246, 247],
        [216, 218, 235],
        [177, 170, 209],
        [128, 115, 172],
        [83, 38, 135],
        [45, 0, 75],
    ],
}  # fmt: skip


# --------------------------------------------------------------------------- store metadata


def _is_url(store: Any) -> bool:
    return isinstance(store, str) and bool(_URL_RE.match(store))


def _store_name(store: str | os.PathLike) -> str:
    return posixpath.basename(str(store).rstrip("/\\")) if _is_url(store) else Path(store).name


def _crs_from_attrs(attrs: dict[str, Any]) -> pyproj.CRS | None:
    for key in ("proj:wkt2", "proj:projjson", "proj:code"):
        value = attrs.get(key)
        if value:
            try:
                return pyproj.CRS.from_user_input(value)
            except Exception:
                continue
    return None


def _crs_config(attrs: dict[str, Any]) -> dict[str, Any]:
    """Name, proj4 string (None if nothing needs registering) and explicit projection name."""
    code = attrs.get("proj:code")
    crs = _crs_from_attrs(attrs)
    proj4 = None
    if crs is not None and code not in _BUILTIN_CRS:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)  # "lose projection information"
                proj4 = crs.to_proj4() or None
        except Exception as err:
            logger.warning("Cannot express the CRS as a proj4 string: {}", err)
    if code:
        return {"code": code, "name": code, "proj4": proj4, "projection": None}
    return {
        "code": None,
        "name": SYNTHETIC_CRS,
        "proj4": proj4,
        "projection": SYNTHETIC_CRS if proj4 else None,
    }


def _labels(values: np.ndarray) -> list[Any] | None:
    if values.size > _MAX_LABELS:
        return None
    if np.issubdtype(values.dtype, np.datetime64):
        return [str(np.datetime_as_string(v, unit="s")).replace("T00:00:00", "") for v in values]
    if values.dtype.kind in "fiu":
        return values.tolist()
    return [str(v) for v in values]


def _clean(x: float) -> float:
    return float(f"{float(x):.10g}")


# --------------------------------------------------------------------------- display helpers

_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_UNIT_ALIASES = {
    "uatm": "µatm",
    "degc": "°C",
    "degree_celsius": "°C",
    "celsius": "°C",
}


def _display_name(long_name: str, name: str) -> str:
    """long_name with simple LaTeX made readable; the variable name when nothing is left."""
    s = str(long_name or "")
    s = re.sub(r"\\(?:mathrm|mathit|mathbf|text|rm)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\Delta\s*", "Δ", s).replace("$", "")
    s = re.sub(r"_\{(\d+)\}", lambda m: m.group(1).translate(_SUB), s)
    s = re.sub(r"_(\d)", lambda m: m.group(1).translate(_SUB), s)
    s = re.sub(r"\^\{(\d+)\}", lambda m: m.group(1).translate(_SUP), s)
    s = re.sub(r"\^(\d)", lambda m: m.group(1).translate(_SUP), s)
    s = " ".join(s.split())
    s = re.sub(r"\s+(?=[₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹])", "", s)  # no stray space before sub/superscripts
    return s or name


def _units_display(units: str) -> str:
    """Typographic spelling of common units ("uatm" -> "µatm", "m yr-1" -> "m yr⁻¹")."""
    u = str(units or "").strip()
    alias = _UNIT_ALIASES.get(u.lower())
    if alias:
        return alias
    return re.sub(r"(?<=[A-Za-z])-(\d)", lambda m: "⁻" + m.group(1).translate(_SUP), u)


def _nice_ticks(vmin: float, vmax: float) -> list[float]:
    """4-6 tick values on 1/2/5 x 10^n steps inside [vmin, vmax]."""
    span = vmax - vmin
    if not (span > 0 and math.isfinite(span)):
        return [_clean(vmin)]
    base = math.floor(math.log10(span))
    best: tuple[float, list[float]] | None = None
    for exp in range(base - 2, base + 2):
        for mant in (1, 2, 5):
            step = mant * 10.0**exp
            k0, k1 = math.ceil(vmin / step - 1e-9), math.floor(vmax / step + 1e-9)
            n = k1 - k0 + 1
            if not 4 <= n <= 6:
                continue
            score = abs(n - 5) - step * 1e-12 / span  # closest to 5, then the coarser step
            if best is None or score < best[0]:
                best = (score, [_clean(k * step) for k in range(k0, k1 + 1)])
    if best is None:  # no 1/2/5 step gives 4-6 ticks; fall back to evenly spaced ones
        return [_clean(vmin + span * i / 4) for i in range(5)]
    return best[1]


def _iso(values: np.ndarray) -> list[str] | None:
    """ISO-8601 (UTC) timestamps for datetime-like coordinates, else None."""
    if values.size > _MAX_LABELS or values.size == 0:
        return None
    try:
        if np.issubdtype(values.dtype, np.datetime64):
            if np.isnat(values).any():
                return None
            return [str(np.datetime_as_string(v, unit="s")) + "Z" for v in values]
        if values.dtype == object and hasattr(values.flat[0], "isoformat"):  # cftime
            return [str(v.isoformat())[:19] + "Z" for v in values]
    except Exception:
        return None
    return None


def _variable_config(name: str, arr: zarr.Array, sdims: list[str]) -> dict[str, Any]:
    dims = list(arr.metadata.dimension_names or ())
    extra = [d for d in dims if d not in sdims]
    index = tuple(0 if d in extra else slice(None) for d in dims)
    data = np.asarray(arr[index])
    finite = np.isfinite(data) if data.dtype.kind == "f" else np.ones(data.shape, bool)
    # D-20: only the effective fill (NaN, a declared fill or the int sentinel) is nodata;
    # 0 stays a valid class. (Zarr bool has no sentinel: its False fill is hidden too.)
    fill = arr.fill_value
    try:
        if fill is not None and not (isinstance(fill, float) and np.isnan(fill)):
            finite &= data != fill
    except (TypeError, ValueError):
        pass
    valid = data[finite].astype("float64")
    attrs = dict(arr.attrs)
    categorical = attrs.get("resampling_method") == "mode" or np.issubdtype(arr.dtype, np.integer)
    cfg: dict[str, Any] = {
        "name": name,
        "dims": extra,
        "categorical": bool(categorical),
        "units": str(attrs.get("units", "")),
        "long_name": str(attrs.get("long_name", "")),
    }
    cfg["display_name"] = _display_name(cfg["long_name"], name)
    cfg["units_display"] = _units_display(cfg["units"])
    if categorical:
        uniq = np.unique(valid)[:_MAX_CATEGORIES]
        cfg["values"] = [_clean(u) for u in uniq]
        cfg["palette"] = [
            [_clean(u), PALETTE[i % len(PALETTE)]] for i, u in enumerate(uniq.tolist())
        ]
        cfg["vmin"] = _clean(uniq[0]) if uniq.size else 0.0
        cfg["vmax"] = _clean(uniq[-1]) if uniq.size else 1.0
        cfg["ticks"] = []
    else:
        if valid.size:
            vmin, vmax = (float(v) for v in np.nanpercentile(valid, [2, 98]))
        else:
            vmin, vmax = 0.0, 1.0
        if vmax <= vmin:
            vmax = vmin + 1.0
        cfg["vmin"], cfg["vmax"] = _clean(vmin), _clean(vmax)
        cfg["ticks"] = _nice_ticks(cfg["vmin"], cfg["vmax"])
    return cfg


def _is_global(attrs: dict[str, Any]) -> bool:
    """True for a geographic CRS whose bbox spans the full 360° of longitude."""
    bbox = attrs.get("spatial:bbox")
    crs = _crs_from_attrs(attrs)
    if not bbox or len(bbox) != 4 or crs is None or not crs.is_geographic:
        return False
    return abs((bbox[2] - bbox[0]) - 360.0) < 1e-6


def _coord_values(group: zarr.Group, name: str) -> np.ndarray | None:
    """CF-decoded values of the 1-D coordinate array ``name`` in ``group``, or None."""
    try:
        arr = group[name]
    except KeyError:
        return None
    if not isinstance(arr, zarr.Array) or arr.ndim != 1:
        return None
    var = xr.Variable(
        list(arr.metadata.dimension_names or (name,)), np.asarray(arr[:]), dict(arr.attrs)
    )
    return xr.conventions.decode_cf_variable(name, var).values


_ATTR_MAX = 2000
_MACHINERY_ATTRS = ("multiscales", "zarr_conventions", VARIABLES_ATTR)


def _attr_value(value: Any) -> str | int | float:
    """A JSON-safe display value: strings and finite numbers as-is, anything else a string."""
    if isinstance(value, (bool, np.bool_)):
        out: str | int | float = str(bool(value))
    elif isinstance(value, (int, np.integer)):
        out = int(value)
    elif isinstance(value, (float, np.floating)):
        out = float(value) if math.isfinite(value) else str(float(value))
    elif isinstance(value, str):
        out = value
    elif isinstance(value, (list, tuple, np.ndarray)):
        items = value.tolist() if isinstance(value, np.ndarray) else value
        out = ", ".join(
            x
            if isinstance(x, str)
            else json.dumps(x, default=str)
            if isinstance(x, dict)
            else str(x)
            for x in items
        )
    elif isinstance(value, dict):
        out = json.dumps(value, default=str)
    else:
        out = str(value)
    if isinstance(out, str) and len(out) > _ATTR_MAX:
        out = out[:_ATTR_MAX] + "…"
    return out


def _global_attrs(attrs: dict[str, Any]) -> dict[str, str | int | float]:
    """Root attrs minus the pyramid/GeoZarr machinery, as JSON-safe display values."""
    return {
        str(k): _attr_value(v)
        for k, v in attrs.items()
        if k not in _MACHINERY_ATTRS and not str(k).startswith(("proj:", "spatial:"))
    }


def read_config(
    store: str | os.PathLike, storage_options: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Collect everything the page needs from the store's root attrs and coarsest level."""
    from .write import _resolve_store

    zstore = _resolve_store(store if _is_url(store) else str(store), storage_options)
    try:
        root = zarr.open_group(zstore, mode="r")
    except Exception as err:
        raise FileNotFoundError(f"No zarr group found at {str(store)!r}.") from err
    attrs = dict(root.attrs)
    layout = (attrs.get("multiscales") or {}).get("layout")
    if not layout:
        raise ValueError(f"{str(store)!r} has no multiscales layout; is it a GeoZarr pyramid?")
    coarsest = str(layout[-1]["asset"])
    group = root[coarsest]
    sdims = list(dict(group.attrs).get("spatial:dimensions") or [])
    variables, dims = [], {}
    order = [str(n) for n in attrs.get(VARIABLES_ATTR) or []]
    # zarr lists arrays alphabetically; keep the source order (unknown names go last)
    arrays = sorted(
        group.arrays(), key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order)
    )
    for name, arr in arrays:
        dn = list(arr.metadata.dimension_names or ())
        if not sdims or not all(d in dn for d in sdims):
            continue
        v = _variable_config(name, arr, sdims)
        variables.append(v)
        for d in v["dims"]:
            if d not in dims:
                # sizes and coordinates come from the (consolidated) group members, never from
                # listing the level: plain HTTP stores cannot list directories
                size = int(arr.shape[dn.index(d)])
                values = _coord_values(group, d)
                labels = _labels(values) if values is not None else None
                iso = _iso(values) if values is not None else None
                dims[d] = {"size": size, "labels": labels, "iso": iso}
    if not variables:
        raise ValueError(f"No plottable variables found in level {coarsest} of {str(store)!r}.")
    crs = _crs_config(attrs)
    return {
        "title": _store_name(store),
        "ol_version": OL_VERSION,
        "variables": variables,
        "dims": dims,
        "crs": crs,
        "bbox": attrs.get("spatial:bbox"),
        "attrs": _global_attrs(attrs),
        "levels": len(layout),
        "global": _is_global(attrs),
        "ramp": RAMP,
        "colormaps": COLORMAPS,
        "basemap_default": crs["code"] in _BUILTIN_CRS,
    }


# --------------------------------------------------------------------------- store checks

_CONVERT_HINT = (
    "Convert it into a GeoZarr pyramid first: `geozarr-pyramid convert {src} {dst}` "
    '(or `ds.geozarr.to_pyramid("{dst}")` in Python), then open {dst}.'
)
_NON_ZARR_SUFFIXES = {
    ".nc": "NetCDF",
    ".nc4": "NetCDF",
    ".cdf": "NetCDF",
    ".h5": "HDF5",
    ".hdf5": "HDF5",
    ".tif": "GeoTIFF",
    ".tiff": "GeoTIFF",
    ".grib": "GRIB",
    ".grib2": "GRIB",
    ".grb": "GRIB",
}


class StoreError(ValueError):
    """A store that cannot be previewed, with a machine-readable ``code`` and a ``hint``."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def __str__(self) -> str:
        return f"{self.message}\n{self.hint}" if self.hint else self.message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


class StoreNotFoundError(StoreError, FileNotFoundError):
    """StoreError for a missing store (also a FileNotFoundError)."""


def _pyramid_name(src: str) -> str:
    stem = src.rstrip("/").rsplit("/", 1)[-1]
    for suffix in (*_NON_ZARR_SUFFIXES, ".zarr"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return f"{stem or 'output'}_pyramid.zarr"


def _convert_hint(src: str) -> str:
    return _CONVERT_HINT.format(src=src, dst=_pyramid_name(src))


def _check_local(path: Path, src: str) -> None:
    if not path.exists():
        raise StoreNotFoundError(
            "not_found",
            f"Nothing exists at {src!r} (resolved to {path}).",
            "Check the spelling; relative paths are resolved from the directory the preview "
            "server was started in.",
        )
    if path.is_file():
        kind = _NON_ZARR_SUFFIXES.get(path.suffix.lower())
        what = f"a {kind} file" if kind else "a file"
        raise StoreError(
            "not_zarr",
            f"{src!r} is {what}, not a Zarr store (Zarr stores are directories).",
            _convert_hint(src),
        )
    if not (path / "zarr.json").is_file():
        if (path / ".zgroup").is_file() or (path / ".zarray").is_file():
            raise StoreError(
                "zarr_v2",
                f"{src!r} is a Zarr v2 store; the preview needs a Zarr v3 GeoZarr pyramid.",
                _convert_hint(src),
            )
        raise StoreError(
            "not_zarr",
            f"{src!r} is a directory but not a Zarr store (no zarr.json inside).",
            "Point at the .zarr directory itself, not its parent or a sub-folder.",
        )
    try:
        node = json.loads((path / "zarr.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise StoreError("bad_metadata", f"{src}/zarr.json cannot be read: {err}.") from err
    if node.get("node_type") == "array":
        raise StoreError(
            "not_group",
            f"{src!r} is a single Zarr array, not a group.",
            "Open the root of the pyramid (the directory holding the level groups 0, 1, 2, …).",
        )


def _storage_options(store: str, endpoint: str | None) -> dict[str, Any] | None:
    """storage_options for a custom S3 endpoint (s3:// stores only; ignored otherwise)."""
    endpoint = (endpoint or "").strip()
    if not endpoint or not store.lower().startswith("s3://"):
        return None
    if not re.match(r"https?://[^/\s]+", endpoint, re.IGNORECASE):
        raise StoreError(
            "bad_endpoint",
            f"The S3 endpoint {endpoint!r} is not an http(s):// URL.",
            "Use a full URL such as https://os.zhdk.cloud.switch.ch.",
        )
    from .write import _s3_endpoint_options

    return _s3_endpoint_options(endpoint)


def _open_root(store: str, storage_options: dict[str, Any] | None = None) -> zarr.Group:
    from .write import _CLOUD_HINT, _resolve_store

    try:
        zstore = _resolve_store(store, storage_options)
    except ImportError as err:
        raise StoreError("missing_dependency", str(err), f"Try: {_CLOUD_HINT}") from err
    except Exception as err:
        raise StoreError("unreachable", f"Cannot open {store!r}: {err}.") from err
    try:
        return zarr.open_group(zstore, mode="r")
    except FileNotFoundError as err:
        raise StoreNotFoundError(
            "not_found",
            f"No Zarr group found at {store!r}.",
            "Check the URL, and that it points at the root of a Zarr v3 store.",
        ) from err
    except PermissionError as err:
        raise StoreError(
            "access_denied",
            f"Access to {store!r} was denied: {err}.",
            "The preview only reads public data (or data your local credentials can read).",
        ) from err
    except Exception as err:
        msg = str(err) or type(err).__name__
        low = msg.lower()
        if "403" in low or "forbidden" in low or "access denied" in low:
            raise StoreError("access_denied", f"Access to {store!r} was denied: {msg}.") from err
        if "404" in low or "not found" in low or "nosuchkey" in low:
            raise StoreNotFoundError("not_found", f"No Zarr group found at {store!r}.") from err
        raise StoreError("unreachable", f"Cannot read {store!r}: {msg}.") from err


def _local_path(src: str) -> str | None:
    """The local path for a plain or ``file://`` path, None for remote URLs."""
    if src.lower().startswith("file://"):
        return src[7:]
    return None if _is_url(src) else src


def display_source(src: str) -> str:
    """How the page shows a store: local paths get a ``file://`` prefix."""
    return src if _is_url(src) else f"file://{src}"


def check_store(
    store: str | os.PathLike, *, label: str | None = None, endpoint: str | None = None
) -> dict[str, Any]:
    """Validate ``store`` for previewing and return its page config, or raise StoreError."""
    src = str(store).strip()
    if not src:
        raise StoreError("empty", "Enter a local path, an https:// URL or an s3:// URL.")
    shown = label or src  # how the user typed it (messages), vs src (what is opened)
    local = _local_path(src)
    if local is not None:
        _check_local(Path(local).expanduser(), shown)
        src_open = str(Path(local).expanduser())
    else:
        src_open = src
    opts = _storage_options(src_open, endpoint)
    root = _open_root(src_open, opts)
    attrs = dict(root.attrs)
    layout = (attrs.get("multiscales") or {}).get("layout")
    if not layout:
        try:
            levels = [k for k, _ in root.groups()]
        except Exception:  # store cannot list (plain HTTP without consolidated metadata)
            levels = []
        extra = f" It has groups {levels[:5]} but no multiscales layout." if levels else ""
        raise StoreError(
            "not_pyramid",
            f"{shown!r} is a Zarr store but not a GeoZarr multiscale pyramid "
            f"(no `multiscales.layout` in its root attributes).{extra}",
            _convert_hint(shown),
        )
    try:
        return read_config(src_open, opts)
    except StoreError:
        raise
    except Exception as err:
        raise StoreError(
            "invalid_pyramid",
            f"{shown!r} looks like a pyramid but cannot be previewed: {err}",
            f"Check it with `geozarr-pyramid validate {shown}`, or re-create it with "
            f"`geozarr-pyramid convert`.",
        ) from err


def browser_url(store: str, endpoint: str | None = None) -> str:
    """The http(s) URL a browser can fetch a remote store from (public buckets only).

    With ``endpoint`` (a custom S3 service), s3:// URLs become path-style URLs on it.
    """
    parts = urlsplit(store)
    scheme = parts.scheme.lower()
    if scheme in ("http", "https"):
        return store
    bucket, key = parts.netloc, parts.path.lstrip("/")
    if scheme == "s3":
        if endpoint and endpoint.strip():
            return f"{endpoint.strip().rstrip('/')}/{bucket}/{quote(key)}"
        return f"https://{bucket}.s3.amazonaws.com/{quote(key)}"
    if scheme in ("gs", "gcs"):
        return f"https://storage.googleapis.com/{bucket}/{quote(key)}"
    raise StoreError(
        "unsupported_scheme",
        f"The browser cannot read {scheme}:// URLs.",
        "Use a local path, an https:// URL or a public s3:// / gs:// URL.",
    )


# --------------------------------------------------------------------------- html


def template_text() -> str:
    return (
        resources.files("geozarr_pyramid_maker")
        .joinpath("templates/preview.html")
        .read_text(encoding="utf-8")
    )


def blank_config() -> dict[str, Any]:
    """Config for the blank viewer: no store, global EPSG:4326 view with a basemap."""
    return {
        "title": "",
        "source": "",
        "store_url": "",
        "ol_version": OL_VERSION,
        "variables": [],
        "dims": {},
        "crs": {"code": "EPSG:4326", "name": "EPSG:4326", "proj4": None, "projection": None},
        "bbox": [-180.0, -90.0, 180.0, 90.0],
        "attrs": {},
        "levels": 0,
        "global": True,
        "ramp": RAMP,
        "colormaps": COLORMAPS,
        "basemap_default": True,
    }


def render_html(config: dict[str, Any] | None = None) -> str:
    """Embed a store config in the standalone viewer; ``None`` gives the blank viewer."""
    if config is None:
        config = blank_config()
    payload = json.dumps(config, allow_nan=False).replace("</", "<\\/")
    page = re.sub(
        r'(<script id="config" type="application/json">).*?(</script>)',
        lambda match: match[1] + payload + match[2],
        template_text(),
        count=1,
        flags=re.DOTALL,
    )
    title = html.escape(config["title"] or "GeoZarr viewer")
    return page.replace(
        "<title>GeoZarr viewer | GeoZarr preview</title>",
        f"<title>{title} | GeoZarr preview</title>",
    )


def _relative_url(target: Path, start: Path) -> str:
    return "./" + Path(os.path.relpath(target.resolve(), start.resolve())).as_posix()


# --------------------------------------------------------------------------- server


class RangeHandler(SimpleHTTPRequestHandler):
    """Static file handler with CORS and single-range HTTP Range support."""

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header(
            "Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges"
        )
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("preview server: " + format, *args)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def parse_request(self) -> bool:
        """Strip the server's base path; requests outside it get a 404 via ``_outside``."""
        ok = super().parse_request()
        base = getattr(self.server, "base_path", "")
        self._outside = False
        self._needs_slash = False
        if ok and base:
            parts = urlsplit(self.path)
            if parts.path == base:
                self._needs_slash = True
            elif parts.path.startswith(base + "/"):
                rest = parts.path[len(base) :]
                self.path = rest + (f"?{parts.query}" if parts.query else "")
            else:
                self._outside = True
        return ok

    def _redirect_or_outside(self) -> bool:
        if getattr(self, "_needs_slash", False):
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", self.server.base_path + "/")  # type: ignore[attr-defined]
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        if getattr(self, "_outside", False):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return True
        return False

    def _redirect_to_start(self) -> bool:
        """302 the viewer root (empty query) to ``?store=…`` when the server has a start store."""
        start = getattr(self.server, "start", None)
        parts = urlsplit(self.path)
        if not start or parts.path != "/" or parts.query:
            return False
        params = {"store": start[0]}
        if start[1]:
            params["endpoint"] = start[1]
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", f"{self.server.base_path}/?{urlencode(params)}")  # type: ignore[attr-defined]
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self) -> None:
        if self._redirect_or_outside():
            return
        path = urlsplit(self.path).path
        if self._redirect_to_start():
            return
        if path == "/api/open":
            self._api_open()
            return
        if path.startswith(f"/{REMOTE_PREFIX}/"):
            self._send_remote(head=False)
            return
        if path in ("/", "/index.html") and getattr(self.server, "blank_viewer", False):
            config = blank_config()
            config["api_base"] = getattr(self.server, "base_path", "") + "/"
            body = render_html(config).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_file(head=False)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_open(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        src = (query.get("store") or [""])[0]
        endpoint = (query.get("endpoint") or [""])[0].strip()
        try:
            # relative local paths resolve from the served directory, not the process cwd
            local = _local_path(src.strip())
            to_check = src
            if local is not None and local and not Path(local).expanduser().is_absolute():
                to_check = str(Path(self.directory) / local)
            config = check_store(to_check, label=src, endpoint=endpoint)
            local = _local_path(src.strip())
            if local is None:
                opts = _storage_options(src.strip(), endpoint)
                config["store_url"] = browser_url(src, endpoint or None)
                config["relay_url"] = _relay(self.server, src.strip(), opts)
                if endpoint and src.strip().lower().startswith("s3://"):
                    config["endpoint"] = endpoint
            else:
                config["store_url"] = _mount(self.server, Path(_local_path(to_check)).expanduser())
        except StoreError as err:
            logger.info("preview: cannot open {!r}: {}", src, err.message)
            self._send_json(HTTPStatus.OK, {"ok": False, "error": err.to_dict()})
            return
        config["source"] = display_source(src.strip())
        self._send_json(HTTPStatus.OK, {"ok": True, "config": config})

    def _send_remote(self, head: bool) -> None:
        """GET/HEAD /remote/<token>/<key...>: read one key of a registered remote store."""
        parts = unquote(urlsplit(self.path).path).split("/")[2:]  # after "", "remote"
        remotes = getattr(self.server, "remotes", {})
        token, key_parts = (parts[0], parts[1:]) if parts else ("", [])
        key = "/".join(key_parts)
        if (
            token not in remotes
            or not key
            or any(p in ("", ".", "..") or "\\" in p or "\0" in p for p in key_parts)
        ):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        header = self.headers.get("Range")
        try:
            data, total, rng = _read_remote(remotes[token], key, header)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        except Exception as err:
            logger.info("preview relay: cannot read {!r}: {}", key, err)
            self.send_error(HTTPStatus.BAD_GATEWAY, "Cannot read the remote store")
            return
        if rng == "bad":
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{total}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status = HTTPStatus.OK if rng is None else HTTPStatus.PARTIAL_CONTENT
        self.send_response(status)
        ctype = "application/json" if key.rsplit("/", 1)[-1] == "zarr.json" else None
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        if rng is not None:
            self.send_header("Content-Range", f"bytes {rng[0]}-{rng[1]}/{total}")
        self.end_headers()
        if not head:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                logger.debug("preview server: client closed the connection")

    def translate_path(self, path: str) -> str:
        mounts = getattr(self.server, "mounts", {})
        parts = urlsplit(path).path.split("/")
        if len(parts) > 2 and parts[1] == MOUNT_PREFIX and parts[2] in mounts:
            base = mounts[parts[2]]
            rel = posixpath.normpath("/".join(parts[3:]) or ".")
            if rel.startswith(".."):
                return str(base / "__outside__")
            return str(base / rel)
        return super().translate_path(path)

    def do_HEAD(self) -> None:
        if self._redirect_or_outside():
            return
        if urlsplit(self.path).path.startswith(f"/{REMOTE_PREFIX}/"):
            self._send_remote(head=True)
            return
        if self._redirect_to_start():
            return
        self._send_file(head=True)

    def _parse_range(self, header: str | None, size: int):
        """Return None (send everything), "bad" (416) or an inclusive (start, end)."""
        if not header or not header.startswith("bytes=") or "," in header:
            return None
        m = re.fullmatch(r"(\d*)-(\d*)", header[6:].strip())
        if not m or (m.group(1) == "" and m.group(2) == ""):
            return None
        first, last = m.groups()
        if first == "":  # suffix
            n = int(last)
            if n == 0 or size == 0:
                return "bad"
            return max(0, size - n), size - 1
        start = int(first)
        end = int(last) if last else size - 1
        if start >= size or (last and end < start):
            return "bad" if start >= size else None
        return start, min(end, size - 1)

    def _send_file(self, head: bool) -> None:
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        size = os.path.getsize(path)
        rng = self._parse_range(self.headers.get("Range"), size)
        if rng == "bad":
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if rng is None:
            start, end, status = 0, size - 1, HTTPStatus.OK
        else:
            (start, end), status = rng, HTTPStatus.PARTIAL_CONTENT
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head or length == 0:
            return
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("preview server: client closed the connection")


MOUNT_PREFIX = "_stores"
REMOTE_PREFIX = "remote"


def _read_remote(store: Any, key: str, header: str | None):
    """Read ``key`` (or one byte range of it) from a zarr Store: (bytes, total, range).

    ``range`` is None (whole object), "bad" (unsatisfiable) or an inclusive (start, end).
    Raises FileNotFoundError for a missing key.
    """
    from zarr.abc.store import OffsetByteRequest, RangeByteRequest, SuffixByteRequest
    from zarr.core.buffer import default_buffer_prototype
    from zarr.core.sync import sync

    proto = default_buffer_prototype()
    m = (
        re.fullmatch(r"(\d*)-(\d*)", header[6:].strip())
        if header and header.startswith("bytes=")
        else None
    )
    if m and (m.group(1) or m.group(2)) and "," not in (header or ""):
        first, last = m.groups()
        total = sync(store.getsize(key)) if _exists(store, key) else None
        if total is None:
            raise FileNotFoundError(key)
        if first == "":
            n = int(last)
            if n == 0 or total == 0:
                return b"", total, "bad"
            start, end = max(0, total - n), total - 1
            req = SuffixByteRequest(n)
        else:
            start = int(first)
            end = min(int(last), total - 1) if last else total - 1
            if start >= total:
                return b"", total, "bad"
            if end < start:  # malformed (last < first): ignore the header like the file handler
                m = None
            req = (
                OffsetByteRequest(start)
                if not last or end == total - 1
                else RangeByteRequest(start, end + 1)
            )
        if m:
            buf = sync(store.get(key, prototype=proto, byte_range=req))
            if buf is None:
                raise FileNotFoundError(key)
            return buf.to_bytes(), total, (start, end)
    buf = sync(store.get(key, prototype=proto))
    if buf is None:
        raise FileNotFoundError(key)
    data = buf.to_bytes()
    return data, len(data), None


def _exists(store: Any, key: str) -> bool:
    from zarr.core.sync import sync

    return bool(sync(store.exists(key)))


def _relay_token(src: str, storage_options: dict[str, Any] | None) -> str:
    raw = src + "\0" + json.dumps(storage_options or {}, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _relay(server: Any, src: str, storage_options: dict[str, Any] | None = None) -> str:
    """Register a remote store for ``/remote/<token>/<key>`` reads; return its URL path.

    The URL carries the server's base path like the ``_mount`` URLs do.
    """
    from .write import _resolve_store

    remotes: dict[str, Any] = server.__dict__.setdefault("remotes", {})
    token = _relay_token(src, storage_options)
    if token not in remotes:
        remotes[token] = _resolve_store(src, storage_options)
    return f"{getattr(server, 'base_path', '')}/{REMOTE_PREFIX}/{token}/"


def _mount(server: Any, path: Path) -> str:
    """Serve a local store under <base>/_stores/<n>/ and return its absolute URL path."""
    mounts: dict[str, Path] = server.__dict__.setdefault("mounts", {})
    base_path = getattr(server, "base_path", "")
    path = path.resolve()
    for key, base in mounts.items():
        if base == path:
            return f"{base_path}/{MOUNT_PREFIX}/{key}/"
    key = str(len(mounts))
    mounts[key] = path
    return f"{base_path}/{MOUNT_PREFIX}/{key}/"


def _norm_base_path(base_path: str | None) -> str:
    """'' or '/a/b' (leading slash, no trailing slash)."""
    b = (base_path or "").strip().strip("/")
    return f"/{b}" if b else ""


def make_server(
    root: str | os.PathLike, port: int = 8000, *, host: str = "127.0.0.1", base_path: str = ""
) -> ThreadingHTTPServer:
    """A threaded server serving ``root`` (use port 0 for a free port).

    ``base_path`` is a URL prefix the server lives under behind a proxy that does not strip it
    (e.g. RenkuLab's ``RENKU_BASE_URL_PATH``); requests are matched with the prefix removed.
    """
    handler = functools.partial(RangeHandler, directory=str(root))
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.base_path = _norm_base_path(base_path)  # type: ignore[attr-defined]
    return server


# --------------------------------------------------------------------------- public


def preview(
    store: str | os.PathLike,
    *,
    out: str | os.PathLike | None = None,
    serve: bool = False,
    port: int = 8000,
    open_browser: bool = False,
    endpoint: str | None = None,
    on_written: Callable[[Path], None] | None = None,
    on_ready: Callable[[str], None] | None = None,
    _server: Any = None,
) -> Path:
    """Write an OpenLayers preview page for ``store`` and optionally serve it locally.

    Remote stores can be served too: the server then relays them (D-31) for buckets the
    browser cannot fetch directly. ``endpoint`` is a custom S3 service URL for s3:// stores.
    """
    remote = _is_url(store)
    endpoint = (endpoint or "").strip() or None
    config = check_store(store, endpoint=endpoint)
    name = config["title"]
    server = _server
    if remote:
        out_path = Path(out) if out else Path.cwd() / f"{name}.preview.html"
        src = str(store)
        config["store_url"] = browser_url(src, endpoint)
        config["source"] = src
        if endpoint and src.lower().startswith("s3://"):
            config["endpoint"] = endpoint
        if serve:
            if server is None:
                server = make_server(out_path.parent.resolve(), port)
            config["relay_url"] = _relay(server, src, _storage_options(src, endpoint))
    else:
        store_path = Path(store).resolve()
        out_path = Path(out) if out else store_path.parent / f"{name}.preview.html"
        config["store_url"] = _relative_url(store_path, out_path.parent)
        config["source"] = display_source(str(store))
    if serve:
        config["api_base"] = getattr(server, "base_path", "") + "/"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(config), encoding="utf-8")
    logger.info("Preview page written to {}", out_path)
    if on_written:
        on_written(out_path)
    if serve:
        serve_page(
            out_path, None if remote else store_path, port=port, open_browser=open_browser,
            on_ready=on_ready, server=server,
        )  # fmt: skip
    return out_path


def serve_viewer(
    root: str | os.PathLike = ".",
    *,
    port: int = 8000,
    host: str = "127.0.0.1",
    base_path: str = "",
    open_browser: bool = False,
    on_ready: Callable[[str], None] | None = None,
    start: tuple[str, str | None] | None = None,
) -> None:
    """Serve the blank viewer at ``<base_path>/`` until Ctrl-C.

    Relative store paths resolve from ``root``. Use ``host="0.0.0.0"`` inside a container.
    ``start`` is a (store, endpoint) pair: the viewer root with an empty query then redirects
    to ``?store=…&endpoint=…`` (D-34); any request with a query string is served unchanged.
    """
    server = make_server(Path(root).resolve(), port, host=host, base_path=base_path)
    server.blank_viewer = True  # type: ignore[attr-defined]
    server.start = start  # type: ignore[attr-defined]
    if start:
        logger.info("Viewer opens on demo store {} (endpoint {})", start[0], start[1])
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{shown}:{server.server_address[1]}{server.base_path}/"  # type: ignore[attr-defined]
    logger.info("Serving the GeoZarr viewer at {} (Ctrl-C to stop)", url)
    if on_ready:
        on_ready(url)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping preview server")
    finally:
        server.server_close()


def serve_page(
    html_path: Path,
    store_path: Path | None,
    *,
    port: int = 8000,
    open_browser: bool = False,
    on_ready: Callable[[str], None] | None = None,
    server: ThreadingHTTPServer | None = None,
) -> None:
    """Serve ``html_path`` and ``store_path`` (via their common parent) until Ctrl-C.

    ``store_path`` is None for a remote store (relayed through ``server``, which must then
    be rooted at the page's directory).
    """
    parents = [html_path.resolve().parent]
    if store_path is not None:
        parents.append(store_path.resolve().parent)
    root = Path(os.path.commonpath(parents))
    if server is None:
        server = make_server(root, port)
    rel = Path(os.path.relpath(html_path.resolve(), root)).as_posix()
    url = f"http://127.0.0.1:{server.server_address[1]}/{rel}"
    logger.info("Serving {} at {} (Ctrl-C to stop)", root, url)
    if on_ready:
        on_ready(url)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping preview server")
    finally:
        server.server_close()
