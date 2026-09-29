"""OpenLayers preview page and a local CORS/Range server (PLAN 4.7)."""

from __future__ import annotations

import functools
import html
import json
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

import numpy as np
import pyproj
import xarray as xr
import zarr
from loguru import logger

from .metadata import VARIABLES_ATTR

OL_VERSION = "10.10.0"
PROJ4_VERSION = "2.22.0"
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
    if categorical:
        uniq = np.unique(valid)[:_MAX_CATEGORIES]
        cfg["values"] = [_clean(u) for u in uniq]
        cfg["palette"] = [
            [_clean(u), PALETTE[i % len(PALETTE)]] for i, u in enumerate(uniq.tolist())
        ]
        cfg["vmin"] = _clean(uniq[0]) if uniq.size else 0.0
        cfg["vmax"] = _clean(uniq[-1]) if uniq.size else 1.0
    else:
        if valid.size:
            vmin, vmax = (float(v) for v in np.nanpercentile(valid, [2, 98]))
        else:
            vmin, vmax = 0.0, 1.0
        if vmax <= vmin:
            vmax = vmin + 1.0
        cfg["vmin"], cfg["vmax"] = _clean(vmin), _clean(vmax)
    return cfg


def read_config(store: str | os.PathLike) -> dict[str, Any]:
    """Collect everything the page needs from the store's root attrs and coarsest level."""
    from .write import _resolve_store

    zstore = _resolve_store(store if _is_url(store) else str(store))
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
    ds = xr.open_zarr(zstore, group=coarsest, consolidated=False)
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
                labels = _labels(ds[d].values) if d in ds.coords else None
                dims[d] = {"size": int(ds.sizes[d]), "labels": labels}
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
        "ramp": RAMP,
        "basemap_default": crs["code"] in _BUILTIN_CRS,
    }


# --------------------------------------------------------------------------- html


def template_text() -> str:
    return (
        resources.files("geozarr_pyramid_maker")
        .joinpath("templates/preview.html")
        .read_text(encoding="utf-8")
    )


def render_html(config: dict[str, Any]) -> str:
    payload = json.dumps(config, allow_nan=False).replace("</", "<\\/")
    return (
        template_text()
        .replace("__TITLE__", html.escape(config["title"]))
        .replace("__OL_VERSION__", OL_VERSION)
        .replace("__PROJ4_VERSION__", PROJ4_VERSION)
        .replace("__CONFIG_JSON__", payload)
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

    def do_GET(self) -> None:
        self._send_file(head=False)

    def do_HEAD(self) -> None:
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


def make_server(root: str | os.PathLike, port: int = 8000) -> ThreadingHTTPServer:
    """A threaded server on 127.0.0.1 serving ``root`` (use port 0 for a free port)."""
    handler = functools.partial(RangeHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


# --------------------------------------------------------------------------- public


def preview(
    store: str | os.PathLike,
    *,
    out: str | os.PathLike | None = None,
    serve: bool = False,
    port: int = 8000,
    open_browser: bool = False,
) -> Path:
    """Write an OpenLayers preview page for ``store`` and optionally serve it locally."""
    remote = _is_url(store)
    if remote and serve:
        raise ValueError("serve=True needs a local store; remote URLs can only get an HTML page.")
    config = read_config(store)
    name = config["title"]
    if remote:
        out_path = Path(out) if out else Path.cwd() / f"{name}.preview.html"
        config["store_url"] = str(store)
    else:
        store_path = Path(store).resolve()
        out_path = Path(out) if out else store_path.parent / f"{name}.preview.html"
        config["store_url"] = _relative_url(store_path, out_path.parent)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(config), encoding="utf-8")
    logger.info("Preview page written to {}", out_path)
    if serve:
        serve_page(out_path, store_path, port=port, open_browser=open_browser)
    return out_path


def serve_page(
    html_path: Path,
    store_path: Path,
    *,
    port: int = 8000,
    open_browser: bool = False,
    on_ready: Callable[[str], None] | None = None,
) -> None:
    """Serve ``html_path`` and ``store_path`` (via their common parent) until Ctrl-C."""
    root = Path(os.path.commonpath([html_path.resolve().parent, store_path.resolve().parent]))
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
