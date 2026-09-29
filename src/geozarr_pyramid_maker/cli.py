"""Command-line interface (real commands arrive in M4)."""

import typer

from . import __version__

app = typer.Typer(
    help="Build GeoZarr multiscale pyramids from regular-grid data.", no_args_is_help=True
)


@app.callback()
def main() -> None:
    """Build GeoZarr multiscale pyramids from regular-grid data."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)
