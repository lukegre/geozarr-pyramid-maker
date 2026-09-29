"""Tests for preview.py (M5): HTML generation and the local CORS/Range server."""

import errno
import json
import re
import threading
import urllib.error
import urllib.request
from importlib import import_module, resources

import numpy as np
import pytest
import xarray as xr
from typer.testing import CliRunner

import geozarr_pyramid_maker as gpm
from geozarr_pyramid_maker.cli import app
from geozarr_pyramid_maker.write import to_pyramid

# `geozarr_pyramid_maker.preview` the attribute is the function; get the module explicitly
pv = import_module("geozarr_pyramid_maker.preview")


def _pyramid(ds, tmp_path, name="data.zarr", **kw):
    store = tmp_path / name
    to_pyramid(ds, store, tile_size=32, validate=False, **kw)
    return store


def _config(html: str) -> dict:
    m = re.search(r'<script id="config" type="application/json">(.*?)</script>', html, re.S)
    assert m, "config script missing"
    return json.loads(m.group(1))


def _coarsest(store):
    import zarr

    layout = zarr.open_group(store, mode="r").attrs["multiscales"]["layout"]
    return xr.open_zarr(store, group=layout[-1]["asset"], consolidated=False)


def _generate(ds, tmp_path, **kw):
    store = _pyramid(ds, tmp_path, **kw)
    out = gpm.preview(store)
    return store, out, out.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- html


def test_polar_html(polar_3031, tmp_path):
    store, out, html = _generate(polar_3031, tmp_path)
    assert out == tmp_path / "data.zarr.preview.html"
    assert not (store / out.name).exists()  # not inside the store
    cfg = _config(html)
    assert cfg["title"] == "data.zarr"
    assert cfg["store_url"] == "./data.zarr"
    assert cfg["crs"]["code"] == "EPSG:3031"
    assert "+proj=stere" in cfg["crs"]["proj4"]
    assert cfg["crs"]["projection"] is None
    assert cfg["basemap_default"] is False
    [v] = cfg["variables"]
    assert v["name"] == "melt" and v["dims"] == [] and not v["categorical"]
    coarse = _coarsest(store)["melt"].values
    lo, hi = np.nanpercentile(coarse, [2, 98])
    assert v["vmin"] == pytest.approx(lo, rel=1e-6)
    assert v["vmax"] == pytest.approx(hi, rel=1e-6)
    assert v["vmin"] < v["vmax"]
    assert f"ol@{pv.OL_VERSION}/source/GeoZarr.js/+esm" in html
    assert f"ol@{pv.OL_VERSION}/ol.css" in html
    assert "file://" not in html


def test_tiny_4326_html(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    cfg = _config(html)
    assert cfg["crs"]["code"] == "EPSG:4326"
    assert cfg["crs"]["proj4"] is None
    assert "+proj=" not in html
    assert cfg["basemap_default"] is True
    assert [v["name"] for v in cfg["variables"]] == ["t"]


def test_multi_var_html(multi_var, tmp_path):
    store, _, html = _generate(multi_var, tmp_path)
    cfg = _config(html)
    by = {v["name"]: v for v in cfg["variables"]}
    assert set(by) == {"melt", "mask"}  # scalar / 1-D variables are not plottable
    assert by["melt"]["dims"] == ["time"]
    assert cfg["dims"]["time"]["size"] == 3
    assert cfg["dims"]["time"]["labels"] == ["2020-01-01", "2020-02-01", "2020-03-01"]
    coarse = _coarsest(store)
    lo, hi = np.nanpercentile(coarse["melt"].isel(time=0).values, [2, 98])
    assert by["melt"]["vmin"] == pytest.approx(lo, rel=1e-6)
    assert by["melt"]["vmax"] == pytest.approx(hi, rel=1e-6)
    mask = by["mask"]
    assert mask["categorical"] and mask["dims"] == []
    assert [p[0] for p in mask["palette"]] == mask["values"]
    assert set(mask["values"]) <= {0.0, 1.0, 2.0}
    assert "vmin" in mask  # still present for the legend fallback


def test_custom_crs_uses_synthetic_name(no_crs_projected, tmp_path):
    crs = "+proj=laea +lat_0=45 +lon_0=10 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    _, _, html = _generate(no_crs_projected, tmp_path, crs=crs)
    cfg = _config(html)["crs"]
    if cfg["code"] is None:
        assert cfg["name"] == pv.SYNTHETIC_CRS
        assert cfg["projection"] == pv.SYNTHETIC_CRS
    else:  # pyproj identified an authority code; the code path is used instead
        assert cfg["projection"] is None
    assert "+proj=laea" in cfg["proj4"]


def test_synthetic_crs_config_without_code():
    from pyproj import CRS

    cfg = pv._crs_config({"proj:wkt2": CRS.from_proj4("+proj=laea +lat_0=1 +datum=WGS84").to_wkt()})
    assert cfg["projection"] == pv.SYNTHETIC_CRS and cfg["name"] == pv.SYNTHETIC_CRS
    assert "+proj=laea" in cfg["proj4"]


def test_explicit_out_and_relative_url(tiny, tmp_path):
    store = _pyramid(tiny, tmp_path)
    out = tmp_path / "pages" / "x.html"
    assert gpm.preview(store, out=out) == out
    assert _config(out.read_text())["store_url"] == "./../data.zarr"


def test_to_pyramid_preview_flag(tiny, tmp_path):
    store = tmp_path / "t.zarr"
    result = to_pyramid(tiny, store, tile_size=32, preview=True)
    assert result.preview_path == tmp_path / "t.zarr.preview.html"
    assert result.preview_path.exists()


def test_template_is_a_package_resource():
    text = resources.files("geozarr_pyramid_maker").joinpath("templates/preview.html").read_text()
    for token in ("__CONFIG_JSON__", "__OL_VERSION__", "__TITLE__"):
        assert token in text


def test_missing_store(tmp_path):
    with pytest.raises(FileNotFoundError):
        gpm.preview(tmp_path / "nope.zarr")


def test_serve_rejects_remote():
    with pytest.raises(ValueError):
        gpm.preview("https://example.com/x.zarr", serve=True)


# --------------------------------------------------------------------------- cli


def test_cli_preview(tiny, tmp_path):
    store = _pyramid(tiny, tmp_path)
    r = CliRunner().invoke(app, ["preview", str(store)])
    assert r.exit_code == 0, r.output
    assert "html:" in r.stdout
    assert (tmp_path / "data.zarr.preview.html").exists()
    out = tmp_path / "o.html"
    assert CliRunner().invoke(app, ["preview", str(store), "--out", str(out)]).exit_code == 0
    assert out.exists()


def test_cli_preview_missing(tmp_path):
    assert CliRunner().invoke(app, ["preview", str(tmp_path / "nope.zarr")]).exit_code == 2


# --------------------------------------------------------------------------- server


@pytest.fixture
def server(tiny, tmp_path):
    store = _pyramid(tiny, tmp_path)
    try:
        srv = pv.make_server(tmp_path, 0)
    except (PermissionError, OSError) as err:
        if isinstance(err, PermissionError) or err.errno in (errno.EPERM, errno.EACCES):
            pytest.skip(f"cannot bind a local port here: {err}")
        raise
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", store
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def _req(url, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_server_get_with_cors(server):
    base, store = server
    status, headers, body = _req(f"{base}/data.zarr/zarr.json")
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "Content-Range" in headers["Access-Control-Expose-Headers"]
    assert headers["Accept-Ranges"] == "bytes"
    assert body == (store / "zarr.json").read_bytes()


def test_server_options(server):
    base, _ = server
    status, headers, body = _req(f"{base}/data.zarr/zarr.json", "OPTIONS")
    assert status == 204 and body == b""
    assert "Range" in headers["Access-Control-Allow-Headers"]
    assert "GET" in headers["Access-Control-Allow-Methods"]


def _big_file(store):
    files = [p for p in store.rglob("*") if p.is_file() and p.stat().st_size > 20]
    return max(files, key=lambda p: p.stat().st_size)


def test_server_ranges(server):
    base, store = server
    f = _big_file(store)
    rel = f.relative_to(store.parent).as_posix()
    data, size = f.read_bytes(), f.stat().st_size
    status, headers, body = _req(f"{base}/{rel}", headers={"Range": "bytes=0-9"})
    assert status == 206 and body == data[:10]
    assert headers["Content-Range"] == f"bytes 0-9/{size}"
    assert headers["Content-Length"] == "10"
    status, headers, body = _req(f"{base}/{rel}", headers={"Range": "bytes=-5"})
    assert status == 206 and body == data[-5:]
    assert headers["Content-Range"] == f"bytes {size - 5}-{size - 1}/{size}"
    status, _, body = _req(f"{base}/{rel}", headers={"Range": "bytes=3-"})
    assert status == 206 and body == data[3:]
    status, _, body = _req(f"{base}/{rel}", headers={"Range": f"bytes={size - 2}-{size + 100}"})
    assert status == 206 and body == data[-2:]
    status, headers, _ = _req(f"{base}/{rel}", headers={"Range": f"bytes={size}-{size + 5}"})
    assert status == 416 and headers["Content-Range"] == f"bytes */{size}"
    status, _, body = _req(f"{base}/{rel}", headers={"Range": "bytes=0-1,5-6"})
    assert status == 200 and body == data


def test_server_404_and_head(server):
    base, store = server
    status, headers, _ = _req(f"{base}/data.zarr/nope/zarr.json")
    assert status == 404 and headers["Access-Control-Allow-Origin"] == "*"
    status, headers, body = _req(f"{base}/data.zarr/zarr.json", "HEAD")
    assert status == 200 and body == b""
    assert int(headers["Content-Length"]) == (store / "zarr.json").stat().st_size


# ------------------------------------------------- handler without a bound port (socketpair)


def _raw(directory, request: bytes) -> tuple[int, dict, bytes]:
    import socket

    a, b = socket.socketpair()
    try:
        a.sendall(request)
        a.shutdown(socket.SHUT_WR)
        pv.RangeHandler(b, ("127.0.0.1", 0), None, directory=str(directory))
        b.close()
        raw = b""
        while chunk := a.recv(65536):
            raw += chunk
    finally:
        a.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:])
    return int(lines[0].split()[1]), headers, body


@pytest.fixture
def datadir(tmp_path):
    (tmp_path / "f.bin").write_bytes(bytes(range(100)))
    return tmp_path


@pytest.mark.parametrize(
    ("rng", "status", "expected", "content_range"),
    [
        ("bytes=0-9", 206, bytes(range(10)), "bytes 0-9/100"),
        ("bytes=-5", 206, bytes(range(95, 100)), "bytes 95-99/100"),
        ("bytes=90-", 206, bytes(range(90, 100)), "bytes 90-99/100"),
        ("bytes=98-500", 206, bytes([98, 99]), "bytes 98-99/100"),
        ("bytes=100-", 416, b"", "bytes */100"),
        ("bytes=0-1,5-6", 200, bytes(range(100)), None),
        ("garbage", 200, bytes(range(100)), None),
    ],
)
def test_handler_ranges_offline(datadir, rng, status, expected, content_range):
    req = f"GET /f.bin HTTP/1.1\r\nHost: x\r\nRange: {rng}\r\n\r\n".encode()
    code, headers, body = _raw(datadir, req)
    assert code == status and body == expected
    assert headers.get("Content-Range") == content_range
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_handler_options_head_404_offline(datadir):
    code, headers, body = _raw(datadir, b"OPTIONS /f.bin HTTP/1.1\r\nHost: x\r\n\r\n")
    assert code == 204 and body == b"" and "Range" in headers["Access-Control-Allow-Headers"]
    code, headers, body = _raw(datadir, b"HEAD /f.bin HTTP/1.1\r\nHost: x\r\n\r\n")
    assert code == 200 and body == b"" and headers["Content-Length"] == "100"
    code, headers, _ = _raw(datadir, b"GET /missing HTTP/1.1\r\nHost: x\r\n\r\n")
    assert code == 404 and headers["Access-Control-Allow-Origin"] == "*"
    code, _, _ = _raw(datadir, b"GET /../etc/passwd HTTP/1.1\r\nHost: x\r\n\r\n")
    assert code == 404
