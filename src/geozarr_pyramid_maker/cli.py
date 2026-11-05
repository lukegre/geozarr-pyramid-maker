"""Command-line interface (``geozarr-pyramid``). The CLI may configure logging (D-08)."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
import xarray as xr
from loguru import logger

from . import __version__
from ._logging import configure_logging
from .detect import DetectionError

app = typer.Typer(
    help="Build GeoZarr multiscale pyramids from regular-grid data.", no_args_is_help=True
)

_state = {"traceback": False}

VarOpt = Annotated[
    list[str] | None, typer.Option("--var", help="Variable to include (repeatable; default: all).")
]
CrsOpt = Annotated[str | None, typer.Option("--crs", help="CRS of the input if not detectable.")]
FillOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--fill-value",
        help="Nodata value of a variable as NAME=VALUE (repeatable; overrides the input's fill).",
    ),
]
ResamplingOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--resampling",
        help="Method for all variables (e.g. mean), or repeatable var=method pairs.",
    ),
]
TileOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--tile-size",
        help="Tile edge length in pixels for all variables (default 512), "
        "and/or repeatable var=pixels pairs.",
    ),
]
KeepbitsOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--keepbits",
        help="Bit rounding: mantissa bits kept (float32 0-23, float64 0-52) for all float "
        "variables and/or repeatable var=bits pairs (default: no rounding).",
    ),
]


class Preset(StrEnum):
    web = "web"
    zstd = "zstd"


PresetOpt = Annotated[
    Preset,
    typer.Option(
        "--preset",
        help="Encoding preset: 'web' (default) writes blosc inner chunks in C order for "
        "browser readers; 'zstd' writes plain zstd chunks in zarr's Morton shard order "
        "(D-63).",
    ),
]
LevelsOpt = Annotated[int | None, typer.Option("--max-levels", help="Maximum number of levels.")]
ShardOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--shard-size",
        help="Shard shape in pixels: N (N x N), or dim:px pairs like time:12,y:2048,x:2048 "
        "(-1 = whole dim; y/x alias the spatial dims); "
        "for all variables and/or repeatable var=SHAPE (default: automatic).",
    ),
]


@app.callback()
def main(
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="-v: DEBUG, -vv: TRACE.")
    ] = 0,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only show warnings.")] = False,
) -> None:
    """Build GeoZarr multiscale pyramids from regular-grid data."""
    level = None
    if quiet:
        level = "WARNING"
    elif verbose >= 2:
        level = "TRACE"
    elif verbose == 1:
        level = "DEBUG"
    _state["traceback"] = verbose >= 2
    configure_logging(level)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@contextmanager
def _clean_errors() -> Iterator[None]:
    """Turn expected errors into a stderr message and exit code 2."""
    try:
        yield
    except (DetectionError, FileExistsError, FileNotFoundError, ValueError) as exc:
        if _state["traceback"]:
            logger.opt(exception=True).error("{}", exc)
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc


def _open_input(path: str) -> xr.Dataset:
    """Open a Zarr store, GeoTIFF or any xarray-readable file as a Dataset."""
    lower = path.lower().rstrip("/")
    if lower.endswith(".zarr"):
        return xr.open_dataset(path, engine="zarr", chunks={})
    if lower.endswith((".tif", ".tiff")):
        import rioxarray

        da = rioxarray.open_rasterio(path, chunks=True)
        if isinstance(da, list):
            raise ValueError(f"{path!r} has several subdatasets; pass a single-raster file.")
        if "band" in da.dims and da.sizes["band"] == 1:
            da = da.squeeze("band", drop=True)
        return da.to_dataset(name=da.name or "band_data")
    return xr.open_dataset(path, chunks={})


def _parse_resampling(values: list[str] | None) -> str | dict[str, str]:
    if not values:
        return "auto"
    pairs = [v for v in values if "=" in v]
    if pairs and len(pairs) != len(values):
        raise ValueError(
            "--resampling: use either one method for all variables or var=method pairs, not both."
        )
    if not pairs:
        if len(values) > 1:
            raise ValueError("--resampling: give a single method, or var=method pairs.")
        return values[0]
    out: dict[str, str] = {}
    for p in pairs:
        name, _, method = p.partition("=")
        if not name or not method:
            raise ValueError(f"--resampling: malformed pair {p!r}; expected var=method.")
        out[name] = method
    return out


def _select(ds: xr.Dataset, var: list[str] | None) -> xr.Dataset:
    if not var:
        return ds
    missing = [v for v in var if v not in ds.data_vars]
    if missing:
        raise ValueError(f"--var: unknown variable(s) {missing}; available: {list(ds.data_vars)}.")
    keep = list(var)
    gm = {ds[v].attrs.get("grid_mapping") or ds[v].encoding.get("grid_mapping") for v in var}
    gm |= {"spatial_ref"}
    keep += [g for g in gm if g in ds.variables and g not in keep and g not in ds.dims]
    return ds[[k for k in keep if k in ds.data_vars]].assign_coords(
        {k: ds[k] for k in keep if k in ds.coords}
    )


def _shard(value: str) -> int | dict[str, int]:
    """'2048' -> 2048, 'time:12,y:2048,x:2048' -> {'time': 12, ...} (pixels)."""
    try:
        if ":" not in value:
            return int(value)
        return {k: int(n) for k, _, n in (p.partition(":") for p in value.split(","))}
    except ValueError:
        raise ValueError(
            f"--shard-size: invalid shape {value!r}; shard size is in pixels "
            "(N or dim:px,dim:px), not bytes."
        ) from None


def _tile(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"--tile-size: invalid value {value!r}; expected an integer.") from None


def _parse_fill_values(values: list[str] | None) -> dict[str, int | float] | None:
    """Repeatable ``NAME=VALUE`` pairs (D-61); VALUE is an int, else a float (``nan`` allowed)."""
    out: dict[str, int | float] = {}
    for v in values or []:
        name, _, text = v.rpartition("=")
        if not name or not text:
            raise ValueError(f"--fill-value: malformed pair {v!r}; expected var=value.")
        try:
            out[name] = int(text)
        except ValueError:
            try:
                out[name] = float(text)
            except ValueError:
                raise ValueError(
                    f"--fill-value: invalid value {text!r} for {name!r}; expected a number."
                ) from None
    return out or None


def _data_var_names(ds: xr.Dataset) -> list[str]:
    """Variables a per-variable mapping can name: >= 2-D data variables (no grid mapping)."""
    return [str(n) for n, v in ds.data_vars.items() if v.ndim >= 2]


def _parse_size_opts[T](
    values: list[str] | None, names: list[str], flag: str, convert: Callable[[str], T], default: T
) -> T | dict[str, T]:
    """A bare default and/or ``NAME=VALUE`` overrides (D-57, D-58).

    Only a default gives a plain value; with overrides, a mapping over ``names`` (default first).
    Unknown override names are kept so ``build_plan`` reports them.
    """
    bare = [v for v in values or [] if "=" not in v]
    if len(bare) > 1:
        raise ValueError(f"{flag}: give at most one default value, plus var=value pairs.")
    base = convert(bare[0]) if bare else default
    pairs = [v for v in values or [] if "=" in v]
    if not pairs:
        return base
    out = dict.fromkeys(names, base)
    for v in pairs:
        name, _, text = v.rpartition("=")
        if not name or not text:
            raise ValueError(f"{flag}: malformed pair {v!r}; expected var=value.")
        out[name] = convert(text)
    return out


def _keepbits(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"--keepbits: invalid value {value!r}; expected an integer.") from None


def _parse_keepbits(values: list[str] | None) -> int | dict[str, int] | None:
    """None without the flag; one bare int for all float variables, or var=bits pairs (D-60)."""
    if not values:
        return None
    pairs = [v for v in values if "=" in v]
    if pairs and len(pairs) != len(values):
        raise ValueError("--keepbits: give either one default value or var=bits pairs, not both.")
    if not pairs:
        if len(values) > 1:
            raise ValueError("--keepbits: give at most one default value.")
        return _keepbits(values[0])
    out: dict[str, int] = {}
    for v in pairs:
        name, _, text = v.rpartition("=")
        if not name or not text:
            raise ValueError(f"--keepbits: malformed pair {v!r}; expected var=bits.")
        out[name] = _keepbits(text)
    return out


@app.command()
def convert(
    input: Annotated[str, typer.Argument(metavar="IN", help="Input file, Zarr store or URL.")],
    output: Annotated[str, typer.Argument(metavar="OUT", help="Output Zarr store.")],
    var: VarOpt = None,
    crs: CrsOpt = None,
    fill_value: FillOpt = None,
    resampling: ResamplingOpt = None,
    tile_size: TileOpt = None,
    max_levels: LevelsOpt = None,
    shard_size: ShardOpt = None,
    keepbits: KeepbitsOpt = None,
    compression_level: Annotated[int, typer.Option("--compression-level")] = 3,
    preset: PresetOpt = Preset.web,
    group: Annotated[
        str | None,
        typer.Option("--group", help="Write the pyramid into this sub-group of OUT (D-62)."),
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing OUT (or just --group).")
    ] = False,
    no_validate: Annotated[bool, typer.Option("--no-validate", help="Skip validation.")] = False,
) -> None:
    """Convert IN into a GeoZarr multiscale pyramid at OUT."""
    t0 = time.perf_counter()
    with _clean_errors():
        method = _parse_resampling(resampling)
        ds = _select(_open_input(input), var)
        names = _data_var_names(ds)
        result = ds.geozarr.to_pyramid(
            output,
            group=group,
            crs=crs,
            fill_values=_parse_fill_values(fill_value),
            resampling=method,
            tile_size=_parse_size_opts(tile_size, names, "--tile-size", _tile, 512),
            max_levels=max_levels,
            shard_size=_parse_size_opts(shard_size, names, "--shard-size", _shard, None),
            keepbits=_parse_keepbits(keepbits),
            compression_level=compression_level,
            encoding_preset=preset.value,
            overwrite=overwrite,
            validate=not no_validate,
        )
    if result.validation is None:
        status = "skipped"
    else:
        errors = [f"{k}: {e}" for k, errs in result.validation.items() for e in errs]
        status = "ok" if not errors else f"FAILED ({len(errors)} error(s))"
    typer.echo(f"path: {result.store}")
    typer.echo(f"levels: {len(result.plan.levels)}")
    typer.echo(f"time: {time.perf_counter() - t0:.1f}s")
    typer.echo(f"validation: {status}")
    if result.validation is not None and errors:
        for e in errors:
            typer.echo(f"  {e}")
        raise typer.Exit(1)


@app.command()
def plan(
    input: Annotated[str, typer.Argument(metavar="IN", help="Input file, Zarr store or URL.")],
    var: VarOpt = None,
    crs: CrsOpt = None,
    fill_value: FillOpt = None,
    resampling: ResamplingOpt = None,
    tile_size: TileOpt = None,
    max_levels: LevelsOpt = None,
    shard_size: ShardOpt = None,
) -> None:
    """Print the pyramid plan for IN without writing anything."""
    with _clean_errors():
        ds = _select(_open_input(input), var)
        names = _data_var_names(ds)
        result = ds.geozarr.plan(
            crs=crs,
            fill_values=_parse_fill_values(fill_value),
            resampling=_parse_resampling(resampling),
            tile_size=_parse_size_opts(tile_size, names, "--tile-size", _tile, 512),
            max_levels=max_levels,
            shard_size=_parse_size_opts(shard_size, names, "--shard-size", _shard, None),
        )
    typer.echo(repr(result))


@app.command()
def validate(
    store: Annotated[str, typer.Argument(help="GeoZarr store to validate.")],
    group: Annotated[
        str | None, typer.Option("--group", help="Validate the pyramid in this sub-group.")
    ] = None,
) -> None:
    """Validate a GeoZarr pyramid; exit 0 if valid, 1 if not, 2 if missing."""
    from .validate import validate as _validate

    if "://" not in store and not Path(store).exists():
        typer.echo(f"Error: store {store!r} does not exist.", err=True)
        raise typer.Exit(2)
    with _clean_errors():
        report: dict[str, Any] = _validate(store, group=group)
    bad = False
    for name, errs in report.items():
        if errs:
            bad = True
            typer.echo(f"{name}: " + "; ".join(errs))
        else:
            typer.echo(f"{name}: ok")
    if bad:
        raise typer.Exit(1)


@app.command()
def preview(
    store: Annotated[
        str | None,
        typer.Argument(help="GeoZarr store (local path or URL). Omit to serve a blank viewer."),
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="HTML output path.")] = None,
    serve: Annotated[bool, typer.Option("--serve", help="Serve the page locally.")] = False,
    port: Annotated[int, typer.Option("--port", help="Port for --serve.")] = 8000,
    open_browser: Annotated[
        bool, typer.Option("--open", help="Open the page in a browser.")
    ] = False,
    host: Annotated[
        str, typer.Option("--host", help="Interface to bind (0.0.0.0 in containers).")
    ] = "127.0.0.1",
    base_path: Annotated[
        str,
        typer.Option(
            "--base-path",
            envvar="RENKU_BASE_URL_PATH",
            help="URL prefix when served behind a proxy (defaults to $RENKU_BASE_URL_PATH).",
        ),
    ] = "",
    endpoint: Annotated[
        str | None,
        typer.Option(
            "--endpoint",
            help="Custom S3 endpoint URL for an s3:// STORE (e.g. https://os.zhdk.cloud.switch.ch).",
        ),
    ] = None,
    demo: Annotated[
        bool,
        typer.Option(
            "--demo", help="Serve the viewer opened on the MUR SST demo store (needs network)."
        ),
    ] = False,
) -> None:
    """Write an OpenLayers preview page for STORE, optionally serving it.

    Without STORE, serve a blank viewer that can open any pyramid.
    """
    from .preview import DEMO_ENDPOINT, DEMO_STORE, serve_viewer
    from .preview import preview as _preview

    if demo:
        clash = [
            n
            for n, v in (
                ("STORE", store),
                ("--endpoint", endpoint),
                ("--out", out),
                ("--serve", serve),
            )
            if v
        ]
        if clash:
            typer.echo(f"Error: --demo cannot be combined with {', '.join(clash)}.", err=True)
            raise typer.Exit(2)
        with _clean_errors():
            serve_viewer(
                ".",
                port=port,
                host=host,
                base_path=base_path,
                open_browser=open_browser,
                on_ready=lambda url: typer.echo(f"url: {url}"),
                start=(DEMO_STORE, DEMO_ENDPOINT),
            )
        return
    if store is None:
        with _clean_errors():
            serve_viewer(
                ".",
                port=port,
                host=host,
                base_path=base_path,
                open_browser=open_browser,
                on_ready=lambda url: typer.echo(f"url: {url}"),
            )
        return
    if "://" not in store and not Path(store).exists():
        typer.echo(f"Error: store {store!r} does not exist.", err=True)
        raise typer.Exit(2)
    with _clean_errors():
        _preview(
            store,
            out=out,
            serve=serve,
            port=port,
            open_browser=open_browser,
            endpoint=endpoint,
            on_written=lambda path: typer.echo(f"html: {path}"),
            on_ready=lambda url: typer.echo(f"url: {url}"),
        )
