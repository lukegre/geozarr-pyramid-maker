import sys

from loguru import logger
from typer.testing import CliRunner

import geozarr_pyramid_maker
from geozarr_pyramid_maker import configure_logging
from geozarr_pyramid_maker.cli import app

runner = CliRunner()


def test_import_and_version():
    assert isinstance(geozarr_pyramid_maker.__version__, str)
    assert geozarr_pyramid_maker.__version__


def test_configure_logging(capsys):
    configure_logging("DEBUG")
    logger.debug("hello-debug")
    assert "hello-debug" in capsys.readouterr().err
    configure_logging("WARNING")
    logger.info("hidden-info")
    assert "hidden-info" not in capsys.readouterr().err


def test_configure_logging_env(monkeypatch, capsys):
    monkeypatch.setenv("LOGURU_LEVEL", "ERROR")
    configure_logging()
    logger.warning("hidden-warning")
    logger.error("shown-error")
    err = capsys.readouterr().err
    assert "hidden-warning" not in err
    assert "shown-error" in err
    assert sys.stderr is not None


def test_cli_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.output


def test_cli_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert geozarr_pyramid_maker.__version__ in result.output
