"""Command-line interface (``geozarr-pyramid``). The CLI may configure logging (D-08)."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
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
ResamplingOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--resampling",
        help="Method for all variables (e.g. mean), or repeatable var=method pairs.",
    ),
]
TileOpt = Annotated[int, typer.Option("--tile-size", help="Tile edge length in pixels.")]
LevelsOpt = Annotated[int | None, typer.Option("--max-levels", help="Maximum number of levels.")]
ShardOpt = Annotated[str, typer.Option("--shard-size", help="Target shard size, e.g. 128MiB.")]


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


def _shard(value: str) -> int | str:
    return int(value) if value.isdigit() else value


@app.command()
def convert(
    input: Annotated[str, typer.Argument(metavar="IN", help="Input file, Zarr store or URL.")],
    output: Annotated[str, typer.Argument(metavar="OUT", help="Output Zarr store.")],
    var: VarOpt = None,
    crs: CrsOpt = None,
    resampling: ResamplingOpt = None,
    tile_size: TileOpt = 512,
    max_levels: LevelsOpt = None,
    shard_size: ShardOpt = "128MiB",
    compression_level: Annotated[int, typer.Option("--compression-level")] = 3,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing OUT.")
    ] = False,
    no_validate: Annotated[bool, typer.Option("--no-validate", help="Skip validation.")] = False,
) -> None:
    """Convert IN into a GeoZarr multiscale pyramid at OUT."""
    t0 = time.perf_counter()
    with _clean_errors():
        method = _parse_resampling(resampling)
        ds = _select(_open_input(input), var)
        result = ds.geozarr.to_pyramid(
            output,
            crs=crs,
            resampling=method,
            tile_size=tile_size,
            max_levels=max_levels,
            shard_size=_shard(shard_size),
            compression_level=compression_level,
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
    resampling: ResamplingOpt = None,
    tile_size: TileOpt = 512,
    max_levels: LevelsOpt = None,
    shard_size: ShardOpt = "128MiB",
) -> None:
    """Print the pyramid plan for IN without writing anything."""
    with _clean_errors():
        ds = _select(_open_input(input), var)
        result = ds.geozarr.plan(
            crs=crs,
            resampling=_parse_resampling(resampling),
            tile_size=tile_size,
            max_levels=max_levels,
            shard_size=_shard(shard_size),
        )
    typer.echo(repr(result))


@app.command()
def validate(store: Annotated[str, typer.Argument(help="GeoZarr store to validate.")]) -> None:
    """Validate a GeoZarr pyramid; exit 0 if valid, 1 if not, 2 if missing."""
    from .validate import validate as _validate

    if "://" not in store and not Path(store).exists():
        typer.echo(f"Error: store {store!r} does not exist.", err=True)
        raise typer.Exit(2)
    with _clean_errors():
        report: dict[str, Any] = _validate(store)
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
