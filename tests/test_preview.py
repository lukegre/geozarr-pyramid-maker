"""Tests for preview.py (M5): HTML generation and the local CORS/Range server."""

import errno
import itertools
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
    assert not cfg["store_url"].startswith("file:")  # data is fetched over http


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
    assert "__CONFIG_JSON__" not in text and "__OL_VERSION__" not in text
    cfg = _config(text)
    assert cfg == pv.blank_config()
    assert "async function browserConfig" in text
    assert "if (!cfg0.api_base) return browserConfig(src, endpoint)" in text


def test_render_embeds_config_without_changing_script(tiny, tmp_path):
    store = _pyramid(tiny, tmp_path)
    cfg = pv.read_config(store)
    cfg["title"] = "A <title> & label"
    cfg["store_url"] = "./t.zarr"
    cfg["attrs"]["description"] = "</script><script>alert(1)</script>"
    page = pv.render_html(cfg)
    assert _config(page) == cfg
    assert "<title>A &lt;title&gt; &amp; label | GeoZarr preview</title>" in page
    assert (
        page.split('<script type="module">')[1]
        == pv.template_text().split('<script type="module">')[1]
    )


def test_view_promise_not_awaited(tiny, tmp_path):
    # Map's `view` option takes Promise<ViewOptions>; awaiting it hands Map a plain object.
    _, _, html = _generate(tiny, tmp_path)
    assert "await getView" not in html
    assert "getView(initial.sources[0]" in html
    assert "state.vlayers.get(state.selected[0]) || makeLayers(cfg.variables[0])" in html


def test_missing_store(tmp_path):
    with pytest.raises(FileNotFoundError):
        gpm.preview(tmp_path / "nope.zarr")


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


def _raw(directory, request: bytes, server=None) -> tuple[int, dict, bytes]:
    import socket

    a, b = socket.socketpair()
    try:
        a.sendall(request)
        a.shutdown(socket.SHUT_WR)

        def handle():
            try:
                pv.RangeHandler(b, ("127.0.0.1", 0), server, directory=str(directory))
            finally:
                b.close()

        thread = threading.Thread(target=handle, daemon=True)
        thread.start()
        raw = b""
        while chunk := a.recv(65536):
            raw += chunk
        thread.join(timeout=5)
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


# --------------------------------------------------------------------------- layout config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Surface $fCO_2$", "Surface fCO₂"),
        ("$\\mathrm{fCO}_{2}$ difference", "fCO₂ difference"),
        ("$\\Delta \\mathrm{pH}$", "ΔpH"),
        ("Area in m^2", "Area in m²"),
        ("Sea   surface  temperature", "Sea surface temperature"),
        ("  padded ", "padded"),
        ("$\\Delta f\\mathrm{CO} _2$", "ΔfCO₂"),
        ("x ^2 and y _{3}", "x² and y₃"),
    ],
)
def test_display_name_cleans_latex(raw, expected):
    assert pv._display_name(raw, "fallback") == expected


@pytest.mark.parametrize("raw", ["", "   ", "$$"])
def test_display_name_falls_back_to_variable_name(raw):
    assert pv._display_name(raw, "sst") == "sst"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("uatm", "µatm"),
        ("degC", "°C"),
        ("degree_Celsius", "°C"),
        ("celsius", "°C"),
        ("m yr-1", "m yr⁻¹"),
        ("mol m-2 s-1", "mol m⁻² s⁻¹"),
        ("m/yr", "m/yr"),
        ("", ""),
        ("K", "K"),
    ],
)
def test_units_display(raw, expected):
    assert pv._units_display(raw) == expected


@pytest.mark.parametrize(
    ("vmin", "vmax"),
    [(0.0, 1.0), (280.0, 470.0), (-3.3, 31.7), (0.0013, 0.0021), (1e6, 9.3e6), (5.0, 5.5)],
)
def test_nice_ticks(vmin, vmax):
    ticks = pv._nice_ticks(vmin, vmax)
    assert 4 <= len(ticks) <= 6
    assert all(vmin <= t <= vmax for t in ticks)
    assert ticks == sorted(ticks)
    steps = {round(b - a, 9) for a, b in itertools.pairwise(ticks)}
    assert len(steps) == 1
    step = steps.pop()
    mant = step / 10 ** np.floor(np.log10(step))
    assert any(np.isclose(mant, m) for m in (1, 2, 5))


def test_config_display_fields(multi_var, tmp_path):
    _, _, html = _generate(multi_var, tmp_path)
    cfg = _config(html)
    by = {v["name"]: v for v in cfg["variables"]}
    assert by["melt"]["display_name"] == "melt"  # no long_name
    assert by["melt"]["units_display"] == "m/yr"
    assert 4 <= len(by["melt"]["ticks"]) <= 6
    assert all(by["melt"]["vmin"] <= t <= by["melt"]["vmax"] for t in by["melt"]["ticks"])
    assert not by["mask"].get("ticks")
    assert cfg["dims"]["time"]["iso"] == [
        "2020-01-01T00:00:00Z",
        "2020-02-01T00:00:00Z",
        "2020-03-01T00:00:00Z",
    ]
    # existing keys are unchanged
    assert by["melt"]["units"] == "m/yr" and by["melt"]["long_name"] == ""


def test_config_dim_iso_null_for_numeric(tmp_path):
    ds = xr.Dataset(
        {"v": (("depth", "lat", "lon"), np.random.default_rng(0).random((3, 64, 64)))},
        coords={
            "depth": [0.0, 10.0, 50.0],
            "lon": -180 + 2.8125 / 2 + 2.8125 * np.arange(64),
            "lat": 90 - 1.40625 / 2 - 2.8125 * np.arange(64) * 0.5,
        },
    ).rio.write_crs("EPSG:4326")
    ds["v"].attrs.update(long_name="Temp $T_2$", units="degC")
    _, _, html = _generate(ds, tmp_path)
    cfg = _config(html)
    assert cfg["dims"]["depth"]["iso"] is None
    [v] = cfg["variables"]
    assert v["display_name"] == "Temp T₂" and v["units_display"] == "°C"


def test_template_layout_hooks(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert "cartocdn" not in html and "nolabels" not in html  # keyless tiles only
    assert "osm('bm-light')" in html and "osm('bm-dark')" in html
    assert ".bm-light canvas" in html and ".bm-dark canvas" in html
    assert "grayscale(1)" in html and "invert(1) hue-rotate(180deg)" in html
    assert "['light', 'Light']" in html and "['dark', 'Dark']" in html
    assert "cfg.basemap_default ? 'dark' : 'none'" in html  # always dark by default
    assert "prefers-color-scheme: dark" in html
    assert "localStorage" in html
    assert "getData(" in html
    assert "overflow-wrap: anywhere" in html and "break-all" not in html
    assert "location.protocol === 'file:'" in html


# Fixed cases mirrored in the template's niceTicks() comment (JS and Python must agree).
_TICK_CASES = [
    ((0.0, 1.0), [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]),
    ((280.0, 470.0), [300.0, 350.0, 400.0, 450.0]),
    ((-3.3, 31.7), [0.0, 10.0, 20.0, 30.0]),
    ((0.0013, 0.0021), [0.0014, 0.0016, 0.0018, 0.002]),
    ((1e6, 9.3e6), [2e6, 4e6, 6e6, 8e6]),
    ((5.0, 5.5), [5.0, 5.1, 5.2, 5.3, 5.4, 5.5]),
    ((2.0, 3.0), [2.0, 2.2, 2.4, 2.6, 2.8, 3.0]),
]


@pytest.mark.parametrize(("args", "expected"), _TICK_CASES)
def test_nice_ticks_fixed_cases(args, expected):
    assert pv._nice_ticks(*args) == pytest.approx(expected, rel=1e-9)


def test_colormaps_config(tiny, tmp_path):
    cm = pv.COLORMAPS
    assert {"viridis", "magma", "inferno", "cividis", "turbo"} <= set(cm)
    assert {"RdBu_r", "coolwarm", "BrBG", "PuOr"} <= set(cm)
    for name, stops in cm.items():
        assert 9 <= len(stops) <= 11, name
        assert all(
            len(c) == 3 and all(isinstance(x, int) and 0 <= x <= 255 for x in c) for c in stops
        )
    assert cm["viridis"][0] == [68, 1, 84] and cm["viridis"][-1] == [253, 231, 37]
    _, _, html = _generate(tiny, tmp_path)
    cfg = _config(html)
    assert cfg["colormaps"] == cm
    assert cfg["ramp"] == pv.RAMP  # kept for back-compat


def test_template_style_controls(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    for token in (
        "setStyle(",
        "Symmetric",
        "Reset",
        "function niceTicks",
        "cfg.colormaps",
        "gpm-preview:style:",
        "type = 'number'",
        "Reversed",
    ):
        assert token in html, token
    assert ".ol-attribution.ol-uncollapsible" in html  # OL's uncollapsible rule sets bottom:0


@pytest.mark.parametrize(
    ("name", "low_is_blue"),
    [("RdBu_r", True), ("coolwarm", True), ("BrBG", False), ("PuOr", False)],
)
def test_diverging_colormap_direction(name, low_is_blue):
    """RdBu_r/coolwarm: low blue, high red; BrBG: low brown, high teal; PuOr: low orange."""
    lo, hi = pv.COLORMAPS[name][0], pv.COLORMAPS[name][-1]
    if low_is_blue:
        assert lo[2] > lo[0] and hi[0] > hi[2]
    else:
        assert lo[0] > lo[2] and hi[2] > hi[0]


def test_template_legend_ticks_and_default_not_reversed(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert "reversed: false" in html  # never reversed by default
    assert "last && units" not in html  # units live in the card's metadata line only
    assert "function layoutTicks" in html


def test_template_reversed_resets_on_colormap_change(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert "styleOf(v).cmap = sel.value; styleOf(v).reversed = false;" in html


def test_global_flag(tiny, polar_3031, tmp_path):
    assert pv._is_global({"spatial:bbox": [-180, -90, 180, 90], "proj:code": "EPSG:4326"})
    assert pv._is_global({"spatial:bbox": [0, -90, 360, 90], "proj:code": "EPSG:4326"})
    assert not pv._is_global({"spatial:bbox": [-10, -90, 180, 90], "proj:code": "EPSG:4326"})
    assert not pv._is_global({"spatial:bbox": [-180, -90, 180, 90], "proj:code": "EPSG:3031"})
    _, _, html = _generate(polar_3031, tmp_path / "p")
    assert _config(html)["global"] is False


def test_template_center_longitude(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert 'id="centerlon"' in html and "if (!cfg.global) return;" in html
    assert "[0, -360, 360]" in html and "cfg.global ? [0, -360, 360]" in html
    assert "wrapX:" not in html


# --------------------------------------------------------------------------- store checks


def _code(src):
    with pytest.raises(pv.StoreError) as info:
        pv.check_store(src)
    return info.value


def test_check_store_not_found(tmp_path):
    err = _code(tmp_path / "nope.zarr")
    assert err.code == "not_found" and isinstance(err, FileNotFoundError)


def test_check_store_netcdf_file_hints_convert(tmp_path):
    f = tmp_path / "sst.nc"
    f.write_bytes(b"CDF\x01")
    err = _code(f)
    assert err.code == "not_zarr" and "NetCDF" in err.message
    assert "geozarr-pyramid convert" in err.hint and "sst_pyramid.zarr" in err.hint


def test_check_store_plain_directory(tmp_path):
    assert _code(tmp_path).code == "not_zarr"


def test_check_store_zarr_v2(tmp_path):
    (tmp_path / "v2.zarr").mkdir()
    (tmp_path / "v2.zarr" / ".zgroup").write_text('{"zarr_format": 2}')
    err = _code(tmp_path / "v2.zarr")
    assert err.code == "zarr_v2" and "convert" in err.hint


def test_check_store_plain_zarr_is_not_pyramid(tiny, tmp_path):
    tiny.to_zarr(tmp_path / "flat.zarr", zarr_format=3, consolidated=False)
    err = _code(tmp_path / "flat.zarr")
    assert err.code == "not_pyramid"
    assert "geozarr-pyramid convert" in err.hint and "to_pyramid" in err.hint


def test_check_store_array_not_group(tmp_path):
    import zarr

    zarr.create_array(tmp_path / "a.zarr", shape=(2,), dtype="f4")
    assert _code(tmp_path / "a.zarr").code == "not_group"


def test_check_store_ok(tiny, tmp_path):
    cfg = pv.check_store(_pyramid(tiny, tmp_path))
    assert cfg["variables"][0]["name"] == "t"


def test_check_store_empty_and_bad_scheme():
    assert _code("  ").code == "empty"
    with pytest.raises(pv.StoreError) as info:
        pv.browser_url("ftp://host/x.zarr")
    assert info.value.code == "unsupported_scheme"


def test_browser_url():
    assert pv.browser_url("s3://bkt/a/b.zarr") == "https://bkt.s3.amazonaws.com/a/b.zarr"
    assert pv.browser_url("gs://bkt/b.zarr") == "https://storage.googleapis.com/bkt/b.zarr"
    assert pv.browser_url("https://x.org/b.zarr") == "https://x.org/b.zarr"


def test_browser_url_with_endpoint():
    ep = "https://os.zhdk.cloud.switch.ch/"
    assert (
        pv.browser_url("s3://bkt/a b/c.zarr", ep)
        == "https://os.zhdk.cloud.switch.ch/bkt/a%20b/c.zarr"
    )
    assert pv.browser_url("gs://bkt/b.zarr", ep) == "https://storage.googleapis.com/bkt/b.zarr"
    assert pv.browser_url("s3://bkt/a.zarr", None) == "https://bkt.s3.amazonaws.com/a.zarr"


def test_storage_options_endpoint():
    assert pv._storage_options("/local/x.zarr", "https://e.org") is None
    assert pv._storage_options("s3://b/k", "") is None
    opts = pv._storage_options("s3://b/k", "https://e.org")
    assert "https://e.org" in opts.values() and set(opts) <= {
        "endpoint",
        "endpoint_url",
        "skip_signature",
        "anon",
    }
    with pytest.raises(pv.StoreError) as info:
        pv._storage_options("s3://b/k", "e.org")
    assert info.value.code == "bad_endpoint" and info.value.hint
    assert _code_endpoint("s3://b/k.zarr", "ftp://e.org").code == "bad_endpoint"


_CRED_VARS = ("AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_WEB_IDENTITY_TOKEN_FILE")


@pytest.mark.parametrize("backend", ["obstore", "fsspec"])
def test_endpoint_options_anonymous_without_credentials(backend, monkeypatch):
    import sys

    from geozarr_pyramid_maker.write import _s3_endpoint_options

    for k in _CRED_VARS:
        monkeypatch.delenv(k, raising=False)
    if backend == "fsspec":
        monkeypatch.setitem(sys.modules, "obstore", None)
        monkeypatch.setitem(sys.modules, "obstore.store", None)
        assert _s3_endpoint_options("https://e.org") == {
            "endpoint_url": "https://e.org",
            "anon": True,
        }
    else:
        pytest.importorskip("obstore")
        assert _s3_endpoint_options("https://e.org") == {
            "endpoint": "https://e.org",
            "skip_signature": True,
        }


@pytest.mark.parametrize("var", _CRED_VARS)
@pytest.mark.parametrize("backend", ["obstore", "fsspec"])
def test_endpoint_options_signed_with_credentials(backend, var, monkeypatch):
    import sys

    from geozarr_pyramid_maker.write import _s3_endpoint_options

    for k in _CRED_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(var, "x")
    if backend == "fsspec":
        monkeypatch.setitem(sys.modules, "obstore", None)
        monkeypatch.setitem(sys.modules, "obstore.store", None)
        assert _s3_endpoint_options("https://e.org") == {"endpoint_url": "https://e.org"}
    else:
        pytest.importorskip("obstore")
        assert _s3_endpoint_options("https://e.org") == {"endpoint": "https://e.org"}


def test_endpoint_options_http_obstore_uses_client_options(monkeypatch):
    from geozarr_pyramid_maker.write import _s3_endpoint_options

    pytest.importorskip("obstore")
    for k in _CRED_VARS:
        monkeypatch.delenv(k, raising=False)
    opts = _s3_endpoint_options("http://example.org")
    assert opts["client_options"] == {"allow_http": True}
    assert "allow_http" not in opts
    assert "client_options" not in _s3_endpoint_options("https://example.org")


def test_endpoint_options_http_obstore_from_url_does_not_panic(monkeypatch):
    from geozarr_pyramid_maker.write import _s3_endpoint_options

    obstore_store = pytest.importorskip("obstore.store")
    for k in _CRED_VARS:
        monkeypatch.delenv(k, raising=False)
    obstore_store.from_url("s3://bucket/key", **_s3_endpoint_options("http://example.org"))


@pytest.mark.network
def test_check_store_public_s3_with_endpoint(monkeypatch):
    for k in _CRED_VARS:
        monkeypatch.delenv(k, raising=False)
    cfg = pv.check_store(
        "s3://spi-pamir-public/test/oceansoda_dfco2.zarr",
        endpoint="https://os.zhdk.cloud.switch.ch",
    )
    assert "dfco2" in [v["name"] for v in cfg["variables"]]


def _code_endpoint(src, endpoint):
    with pytest.raises(pv.StoreError) as info:
        pv.check_store(src, endpoint=endpoint)
    return info.value


def _open_api(directory, query):
    from types import SimpleNamespace

    srv = SimpleNamespace(base_path="", mounts={})
    req = f"GET /api/open?{query} HTTP/1.1\r\nHost: x\r\n\r\n".encode()
    code, _, body = _raw(directory, req, srv)
    assert code == 200
    return json.loads(body)


def test_api_open_passes_endpoint(tmp_path, monkeypatch):
    seen = {}

    def fake_check(store, *, label=None, endpoint=None):
        seen.update(store=store, endpoint=endpoint)
        return {"variables": [], "title": "t"}

    import zarr

    monkeypatch.setattr(pv, "check_store", fake_check)
    monkeypatch.setattr(
        "geozarr_pyramid_maker.write._resolve_store", lambda s, o=None: zarr.storage.MemoryStore()
    )
    cfg = _open_api(tmp_path, "store=s3://bkt/a.zarr&endpoint=https://e.org")["config"]
    assert seen == {"store": "s3://bkt/a.zarr", "endpoint": "https://e.org"}
    assert cfg["source"] == "s3://bkt/a.zarr" and cfg["endpoint"] == "https://e.org"
    assert cfg["store_url"] == "https://e.org/bkt/a.zarr"
    assert "endpoint" not in _open_api(tmp_path, "store=s3://bkt/a.zarr")["config"]


def test_api_open_bad_endpoint(tmp_path):
    err = _open_api(tmp_path, "store=s3://bkt/a.zarr&endpoint=nope")
    assert err["ok"] is False and err["error"]["code"] == "bad_endpoint" and err["error"]["hint"]


def test_template_info_panel(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert "settingsbtn" not in html and "settingspanel" not in html
    assert 'id="topbar"' in html and 'id="infobtn"' in html and 'id="infopanel"' in html
    assert 'aria-label="Dataset information"' in html
    panel = html.split('id="infopanel"', 1)[1].split("</div>", 1)[0]
    assert "s3row" not in panel and "endpoint" not in panel
    assert html.index('id="s3row"') > html.index('id="topbar"')
    assert html.index('id="s3row"') < html.index('id="cards"')
    assert 'id="endpoint"' in html and "S3 endpoint URL" in html
    assert "os.zhdk.cloud.switch.ch" in html
    assert "api/open?store=" in html and "&endpoint=" in html
    assert "q.set('endpoint'" in html
    assert "Global attributes" in html and "cfg.attrs" in html


def test_cli_preview_explains_bad_store(tiny, tmp_path):
    tiny.to_zarr(tmp_path / "flat.zarr", zarr_format=3, consolidated=False)
    res = CliRunner().invoke(app, ["preview", str(tmp_path / "flat.zarr")])
    assert res.exit_code == 2
    assert "not a GeoZarr multiscale pyramid" in res.output and "convert" in res.output


def test_server_api_open(server, tmp_path):
    base, store = server
    from urllib.parse import quote

    status, _, body = _req(f"{base}/api/open?store={quote(str(store))}")
    payload = json.loads(body)
    assert status == 200 and payload["ok"] is True
    url = payload["config"]["store_url"]
    assert url.startswith("/_stores/") and payload["config"]["source"] == f"file://{store}"
    status, _, meta = _req(base + url + "zarr.json")
    assert status == 200 and b"multiscales" in meta
    assert _req(base + url + "../../etc/passwd")[0] == 404

    status, _, body = _req(f"{base}/api/open?store={quote(str(tmp_path / 'missing.zarr'))}")
    err = json.loads(body)
    assert err["ok"] is False and err["error"]["code"] == "not_found" and err["error"]["hint"]


def test_template_store_picker(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert 'id="storepath"' in html and 'id="storeerr"' in html
    assert "api/open?store=" in html and "browser_blocked" in html and "api_base" in html
    assert "searchParams.set('store'" in html


def test_template_topbar(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert "#storepath:focus, #storepath.busy {" not in html
    rule = html.split("#topbar {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in rule and "left: 0" in rule and "right: 0" in rule
    assert "--top-h" in html and "body.collapsed { --sb-w: 44px; --top-h: 0px; }" in html
    assert html.index('id="topbar"') < html.index('id="storepath"') < html.index('id="infobtn"')
    assert "state.map?.updateSize()" in html


def test_template_url_state(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert "history.replaceState" in html and "function syncUrl" in html
    for key in ("'store'", "'var'", "'x'", "'z'"):
        assert f"q.set({key}" in html
    for key in (
        "'cmap'",
        "'rev'",
        "'vmin'",
        "'vmax'",
        "'opacity'",
    ):  # via styleParams (suffixed per var)
        assert f"key({key})" in html
    assert "applyUrlState" in html and "state.map.on('moveend', syncUrl)" in html
    assert "q.set('var', state.selected.join(','))" in html


def test_template_multi_variable_selection(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert "state.active" not in html and "optionsOpen" not in html and "state.layers" not in html
    assert "state.selected" in html and "state.vlayers" in html and "state.open" in html
    assert (
        "function toggleVar" in html
        and "function addVariable" in html
        and "function removeVariable" in html
    )
    assert "function toggleVisibility" in html
    assert "state.hidden" in html
    assert "state.selected.length === 1" not in html  # every card can be removed
    assert "function unionDims" in html or "const unionDims" in html
    assert "function styleParams" in html and "`${k}.${v.name}`" in html
    assert "state.opacity[v.name]" in html and 'id="selhint"' in html
    assert "varOf(vn).dims.includes(name)" in html  # setDim only touches variables with that dim
    assert "[...state.selected].reverse()" in html  # readout: top-most first


def test_template_collapsed_spine(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert 'id="sbspine"' in html and "writing-mode: vertical-rl" in html
    assert "function updateSpine" in html and "body.collapsed #sbspine" in html


def test_file_prefix(tiny, tmp_path):
    store = _pyramid(tiny, tmp_path)
    assert pv.check_store(f"file://{store}")["variables"]
    assert pv.display_source("a/b.zarr") == "file://a/b.zarr"
    assert pv.display_source("https://x/b.zarr") == "https://x/b.zarr"
    assert _config(gpm.preview(store).read_text())["source"] == f"file://{store}"


def test_blank_viewer_html():
    html = pv.render_html(None)
    cfg = _config(html)
    assert cfg["variables"] == [] and cfg["global"] is True and cfg["basemap_default"] is True
    assert cfg["crs"]["code"] == "EPSG:4326" and cfg["bbox"] == [-180.0, -90.0, 180.0, 90.0]
    assert "__" not in re.sub(r"__proto__", "", html.split('<script type="module">')[0])
    assert "const blank = " in html and "GeoZarr viewer" in html


def test_server_serves_blank_viewer(server):
    base, _ = server
    assert _req(base + "/")[0] == 404  # plain page server: no viewer at /


def test_serve_viewer_root(tmp_path):
    try:
        srv = pv.make_server(tmp_path, 0)
    except OSError as err:
        pytest.skip(f"cannot bind a local port here: {err}")
    srv.blank_viewer = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _req(f"http://127.0.0.1:{srv.server_address[1]}/")
        assert status == 200 and "text/html" in headers["Content-Type"]
        assert _config(body.decode())["variables"] == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_template_cleared_path_goes_blank(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert "wanted === ''" in html and "?store=`" in html
    assert "if (blank) q.set('store', '')" in html


def test_viewer_under_base_path(tiny, tmp_path):
    from urllib.parse import quote

    store = _pyramid(tiny, tmp_path)
    try:
        srv = pv.make_server(tmp_path, 0, base_path="/sessions/abc/")
    except OSError as err:
        pytest.skip(f"cannot bind a local port here: {err}")
    srv.blank_viewer = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        status, _, body = _req(base + "/sessions/abc/")
        assert status == 200 and _config(body.decode())["api_base"] == "/sessions/abc/"
        assert _req(base + "/")[0] == 404  # outside the prefix
        status, _, body = _req(f"{base}/sessions/abc/api/open?store={quote(store.name)}")
        url = json.loads(body)["config"]["store_url"]
        assert url.startswith("/sessions/abc/_stores/")
        assert _req(base + url + "zarr.json")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_norm_base_path():
    assert pv._norm_base_path(None) == "" and pv._norm_base_path("/") == ""
    assert pv._norm_base_path("a/b/") == "/a/b" and pv._norm_base_path("/a") == "/a"


# --------------------------------------------------------------------------- relay (D-31)

_ZJSON = b'{"zarr_format": 3, "node_type": "group"}'
_BLOB = bytes(range(100))


@pytest.fixture
def relay_server(tmp_path):
    """A stand-in server (no port) with a local zarr store registered under a token."""
    from types import SimpleNamespace

    import zarr

    root = tmp_path / "remote.zarr"
    (root / "0" / "c").mkdir(parents=True)
    (root / "zarr.json").write_bytes(_ZJSON)
    (root / "0" / "c" / "0").write_bytes(_BLOB)
    srv = SimpleNamespace(
        base_path="", mounts={}, remotes={"tok123": zarr.storage.LocalStore(root)}
    )
    return srv, tmp_path


def _relay_req(fx, path, method="GET", rng=None):
    srv, directory = fx
    extra = f"Range: {rng}\r\n" if rng else ""
    req = f"{method} {path} HTTP/1.1\r\nHost: x\r\n{extra}\r\n".encode()
    return _raw(directory, req, srv)


def test_relay_full_get(relay_server):
    code, headers, body = _relay_req(relay_server, "/remote/tok123/zarr.json")
    assert code == 200 and body == _ZJSON
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Access-Control-Allow-Origin"] == "*"
    code, headers, body = _relay_req(relay_server, "/remote/tok123/0/c/0")
    assert code == 200 and body == _BLOB
    assert headers["Content-Type"] == "application/octet-stream"


@pytest.mark.parametrize(
    ("rng", "expected", "content_range"),
    [
        ("bytes=2-5", _BLOB[2:6], "bytes 2-5/100"),
        ("bytes=-4", _BLOB[-4:], "bytes 96-99/100"),
        ("bytes=3-", _BLOB[3:], "bytes 3-99/100"),
        ("bytes=98-500", _BLOB[98:], "bytes 98-99/100"),
    ],
)
def test_relay_ranges(relay_server, rng, expected, content_range):
    code, headers, body = _relay_req(relay_server, "/remote/tok123/0/c/0", rng=rng)
    assert code == 206 and body == expected
    assert headers["Content-Range"] == content_range
    assert headers["Content-Length"] == str(len(expected))
    assert headers["Accept-Ranges"] == "bytes"


def test_relay_head(relay_server):
    code, headers, body = _relay_req(relay_server, "/remote/tok123/0/c/0", method="HEAD")
    assert code == 200 and body == b"" and headers["Content-Length"] == "100"
    code, headers, body = _relay_req(
        relay_server, "/remote/tok123/0/c/0", method="HEAD", rng="bytes=-4"
    )
    assert code == 206 and body == b"" and headers["Content-Range"] == "bytes 96-99/100"


def test_relay_not_found_and_traversal(relay_server):
    assert _relay_req(relay_server, "/remote/tok123/nope/zarr.json")[0] == 404
    assert _relay_req(relay_server, "/remote/other/zarr.json")[0] == 404
    assert _relay_req(relay_server, "/remote/tok123/")[0] == 404  # no listing
    assert _relay_req(relay_server, "/remote/tok123/0/../zarr.json")[0] in (400, 404)
    assert _relay_req(relay_server, "/remote/tok123/%2e%2e/zarr.json")[0] in (400, 404)
    assert _relay_req(relay_server, "/remote/tok123/0/..%2f..%2fx")[0] in (400, 404)


def test_relay_backend_error_is_502(relay_server):
    srv, _ = relay_server

    class Broken:
        async def get(self, *a, **k):
            raise RuntimeError("boom")

        async def getsize(self, key):
            raise RuntimeError("boom")

    srv.remotes["bad"] = Broken()
    assert _relay_req(relay_server, "/remote/bad/zarr.json")[0] == 502


def test_relay_registers_stable_token(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import zarr

    monkeypatch.setattr(
        "geozarr_pyramid_maker.write._resolve_store", lambda s, o=None: zarr.storage.MemoryStore()
    )
    srv = SimpleNamespace(base_path="/sessions/abc")
    url = pv._relay(srv, "s3://bkt/a.zarr", {"endpoint": "https://e.org"})
    assert re.fullmatch(r"/sessions/abc/remote/[0-9a-f]{12}/", url)
    assert pv._relay(srv, "s3://bkt/a.zarr", {"endpoint": "https://e.org"}) == url
    assert pv._relay(srv, "s3://bkt/b.zarr", {"endpoint": "https://e.org"}) != url
    assert len(srv.remotes) == 2


def test_api_open_returns_relay_url(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import zarr

    seen = {}

    def fake_check(store, *, label=None, endpoint=None):
        return {"variables": [], "title": "t"}

    def fake_resolve(store, options=None):
        seen.update(store=store, options=options)
        return zarr.storage.MemoryStore()

    monkeypatch.setattr(pv, "check_store", fake_check)
    monkeypatch.setattr("geozarr_pyramid_maker.write._resolve_store", fake_resolve)
    srv = SimpleNamespace(base_path="", mounts={})
    q = "store=s3://bkt/a.zarr&endpoint=https://e.org"
    code, _, body = _raw(tmp_path, f"GET /api/open?{q} HTTP/1.1\r\nHost: x\r\n\r\n".encode(), srv)
    cfg = json.loads(body)["config"]
    assert code == 200
    assert cfg["store_url"] == "https://e.org/bkt/a.zarr"  # direct stays
    assert re.fullmatch(r"/remote/[0-9a-f]{12}/", cfg["relay_url"])
    assert seen["store"] == "s3://bkt/a.zarr" and "https://e.org" in seen["options"].values()
    assert cfg["relay_url"].split("/")[2] in srv.remotes


def test_api_open_local_store_has_no_relay(tiny, tmp_path):
    store = _pyramid(tiny, tmp_path)
    cfg = _open_api(tmp_path, f"store={store.name}")["config"]
    assert "relay_url" not in cfg


def test_template_relay_fallback(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert "relay_url" in html and "relayed" in html
    assert (
        "Relayed through the preview server (the bucket does not allow direct browser access)"
        in html
    )
    server_loader = html.split("async function openStore(src, endpoint)")[1]
    assert "relay" in server_loader.split("'browser_blocked'")[0].split("storeError(")[-1]


def test_cli_preview_endpoint_passed_through(tmp_path, monkeypatch):
    seen = {}

    def fake_preview(store, **kw):
        seen.update(store=store, **kw)
        return tmp_path / "x.html"

    monkeypatch.setattr(pv, "preview", fake_preview)
    r = CliRunner().invoke(
        app, ["preview", "s3://bkt/a.zarr", "--endpoint", "https://e.org", "--out", "o.html"]
    )
    assert r.exit_code == 0, r.output
    assert seen["endpoint"] == "https://e.org"


def test_preview_remote_bakes_endpoint_and_relay(tmp_path, monkeypatch):
    import zarr

    seen = {}

    def fake_check(store, *, label=None, endpoint=None):
        seen["endpoint"] = endpoint
        return {"variables": [], "title": "a.zarr", "crs": {}, "bbox": None}

    monkeypatch.setattr(pv, "check_store", fake_check)
    monkeypatch.setattr(
        "geozarr_pyramid_maker.write._resolve_store", lambda s, o=None: zarr.storage.MemoryStore()
    )
    from types import SimpleNamespace

    served = []
    fake_srv = SimpleNamespace(
        base_path="",
        mounts={},
        server_address=("127.0.0.1", 1),
        serve_forever=lambda: served.append(True),
        server_close=lambda: None,
    )
    out = pv.preview(
        "s3://bkt/a.zarr",
        out=tmp_path / "o.html",
        endpoint="https://e.org",
        serve=True,
        _server=fake_srv,
    )
    assert served
    cfg = _config(out.read_text(encoding="utf-8"))
    assert seen["endpoint"] == "https://e.org"
    assert cfg["endpoint"] == "https://e.org"
    assert cfg["store_url"] == "https://e.org/bkt/a.zarr"
    assert cfg["relay_url"].startswith("/remote/")


@pytest.mark.network
def test_relay_serves_real_switch_store(tmp_path, monkeypatch):
    from types import SimpleNamespace

    for k in _CRED_VARS:
        monkeypatch.delenv(k, raising=False)
    src, endpoint = (
        "s3://spi-pamir-public/test/oceansoda_dfco2.zarr",
        "https://os.zhdk.cloud.switch.ch",
    )
    srv = SimpleNamespace(base_path="", mounts={})
    url = pv._relay(srv, src, pv._storage_options(src, endpoint))
    code, _, body = _raw(tmp_path, f"GET {url}zarr.json HTTP/1.1\r\nHost: x\r\n\r\n".encode(), srv)
    assert code == 200 and json.loads(body)["zarr_format"] == 3


# --------------------------------------------------------------------------- non-listable stores


def _unlistable(path):
    """A LocalStore that cannot list, like a plain HTTP store (only consolidated metadata helps)."""
    from zarr.storage import LocalStore

    class NoListStore(LocalStore):
        supports_listing = False

        def list(self):
            raise NotImplementedError("no listing over HTTP")

        def list_prefix(self, prefix):
            raise NotImplementedError("no listing over HTTP")

        def list_dir(self, prefix):
            raise NotImplementedError("no listing over HTTP")

    return NoListStore(path, read_only=True)


def test_read_config_does_not_need_listing(multi_var, tmp_path, monkeypatch):
    from geozarr_pyramid_maker import write

    store = _pyramid(multi_var, tmp_path)
    expected = pv.read_config(store)
    assert expected["dims"]["time"]["iso"]  # time decoded, so the comparison means something
    monkeypatch.setattr(write, "_resolve_store", lambda s, opts=None: _unlistable(str(s)))
    assert pv.read_config(store) == expected


@pytest.mark.network
def test_read_config_https_store_without_listing():
    cfg = pv.read_config(
        "https://os.zhdk.cloud.switch.ch/spi-pamir-public/test/oceansoda_dfco2.zarr"
    )
    assert "dfco2" in [v["name"] for v in cfg["variables"]]
    assert "time" in cfg["dims"]


# --------------------------------------------------------------------------- attrs and levels


def test_global_attrs_filters_machinery():
    attrs = {
        "title": "T",
        "institution": "ETH",
        "multiscales": {"layout": []},
        "zarr_conventions": [],
        "proj:code": "EPSG:4326",
        "spatial:bbox": [0, 0, 1, 1],
        pv.VARIABLES_ATTR: ["a"],
    }
    assert pv._global_attrs(attrs) == {"title": "T", "institution": "ETH"}
    assert list(pv._global_attrs({"b": 1, "a": 2})) == ["b", "a"]


def test_global_attrs_json_safe():
    out = pv._global_attrs(
        {
            "nan": float("nan"),
            "inf": float("inf"),
            "n": 3,
            "f": 1.5,
            "np": np.float32(2.5),
            "npi": np.int64(4),
            "list": ["a", 1, 2.5],
            "dict": {"k": float("nan")},
            "none": None,
            "flag": True,
        }
    )
    json.dumps(out, allow_nan=False)
    assert out["n"] == 3 and out["f"] == 1.5 and out["np"] == 2.5 and out["npi"] == 4
    assert out["nan"] == "nan" and out["inf"] == "inf"
    assert out["list"] == "a, 1, 2.5"
    assert isinstance(out["dict"], str) and "k" in out["dict"]
    assert isinstance(out["none"], str) and isinstance(out["flag"], str)


def test_global_attrs_truncates():
    out = pv._global_attrs({"long": "x" * 5000, "ok": "y" * 2000})
    assert len(out["long"]) == 2001 and out["long"].endswith("…")
    assert out["ok"] == "y" * 2000


def test_read_config_attrs_and_levels(tiny, tmp_path):
    ds = tiny.copy()
    ds.attrs.update(title="My title", institution="SDSC", bad=float("nan"))
    store = _pyramid(ds, tmp_path)
    cfg = pv.read_config(store)
    assert cfg["attrs"]["title"] == "My title" and cfg["attrs"]["institution"] == "SDSC"
    assert not {"multiscales", "zarr_conventions", pv.VARIABLES_ATTR} & set(cfg["attrs"])
    assert not any(k.startswith(("proj:", "spatial:")) for k in cfg["attrs"])
    import zarr

    n = len(zarr.open_group(store, mode="r").attrs["multiscales"]["layout"])
    assert cfg["levels"] == n >= 1
    json.dumps(cfg, allow_nan=False)
    assert _config(gpm.preview(store).read_text())["attrs"]["title"] == "My title"


def test_blank_config_attrs_levels():
    cfg = pv.blank_config()
    assert cfg["attrs"] == {} and cfg["levels"] == 0
    assert _config(pv.render_html(None))["levels"] == 0


# ------------------------------------------------------------- start store redirect (D-34)


def _viewer_srv(start, base_path=""):
    from types import SimpleNamespace

    return SimpleNamespace(base_path=base_path, mounts={}, blank_viewer=True, start=start)


START = ("s3://bkt/a b.zarr", "https://e.org")


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_start_redirects_empty_query(tmp_path, method):
    req = f"{method} / HTTP/1.1\r\nHost: x\r\n\r\n".encode()
    code, headers, _ = _raw(tmp_path, req, _viewer_srv(START))
    assert code == 302
    assert headers["Location"] == "/?store=s3%3A%2F%2Fbkt%2Fa+b.zarr&endpoint=https%3A%2F%2Fe.org"


def test_start_redirect_without_endpoint(tmp_path):
    req = b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"
    code, headers, _ = _raw(tmp_path, req, _viewer_srv(("s3://b/a.zarr", None)))
    assert code == 302 and headers["Location"] == "/?store=s3%3A%2F%2Fb%2Fa.zarr"


def test_start_redirect_with_base_path(tmp_path):
    req = b"GET /pre/ HTTP/1.1\r\nHost: x\r\n\r\n"
    code, headers, _ = _raw(tmp_path, req, _viewer_srv(START, "/pre"))
    assert code == 302
    assert headers["Location"].startswith("/pre/?store=s3%3A%2F%2Fbkt")


@pytest.mark.parametrize("query", ["?store=", "?store=x", "?var=a"])
def test_start_not_redirected_with_query(tmp_path, query):
    req = f"GET /{query} HTTP/1.1\r\nHost: x\r\n\r\n".encode()
    code, _, body = _raw(tmp_path, req, _viewer_srv(START))
    assert code == 200 and b"<html" in body.lower()


def test_no_start_no_redirect(tmp_path):
    req = b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"
    code, _, _ = _raw(tmp_path, req, _viewer_srv(None))
    assert code == 200


def test_demo_constants():
    assert pv.DEMO_STORE == "s3://spi-greenfjord-public/test/mur_sst_subset.zarr"
    assert pv.DEMO_ENDPOINT == "https://os.zhdk.cloud.switch.ch"


def test_template_sidebar_sections(tiny, tmp_path):
    _, _, html = _generate(tiny, tmp_path)
    assert html.index('id="s3row"') < html.index('id="cards"')
    assert html.index('id="cards"') < html.index('id="sec-selected"') < html.index('id="sec-other"')
    assert 'id="sel-cards"' in html and 'id="other-cards"' in html
    assert (
        'id="other-toggle"' in html
        and "aria-expanded" in html.split('id="other-toggle"', 1)[1][:200]
    )
    assert "const OTHER_KEY = 'gpm-preview:other-collapsed'" in html
    assert "let otherCollapsed = store.get(OTHER_KEY) === '1'" in html
    assert "store.set(OTHER_KEY, otherCollapsed" in html
    body = html.split("function refreshCards()", 1)[1].split("\nfunction ", 1)[0]
    assert "[...state.selected].reverse()" in body
    assert "append(" in body and "Selected (" in body and "Other (" in body


def test_viewer_selection_drag_behaviour():
    """Run viewer handlers for visibility, removal, drag order, labels and layer reuse."""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute viewer interaction tests")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            node,
            str(root / "tests/preview_selection.cjs"),
            str(root / "src/geozarr_pyramid_maker/templates/preview.html"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_viewer_uses_array_names():
    html = pv.render_html(None)
    assert "top.append(handle, h('div', 'name', v.name))" in html
    assert "v.display_name || v.name" not in html
    assert "if (v.long_name?.trim()) opts.append" in html
    assert "v.display_name || v.long_name" in html
    assert ".card.selected.open .opts" in html
    assert "params.get('var') === ''" in html
    assert "q.set('hidden', hidden.join(','))" in html
