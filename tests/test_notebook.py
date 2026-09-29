"""Execute examples/demo.ipynb end to end (needs network)."""

import shutil
from pathlib import Path

import nbformat
import pytest
from nbclient import NotebookClient

pytestmark = [pytest.mark.slow, pytest.mark.network]

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_demo_notebook_runs(tmp_path):
    work = tmp_path / "examples"
    shutil.copytree(EXAMPLES, work, ignore=shutil.ignore_patterns("*.zarr", "__pycache__"))
    nb = nbformat.read(work / "demo.ipynb", as_version=4)
    client = NotebookClient(
        nb, timeout=600, kernel_name="python3", resources={"metadata": {"path": str(work)}}
    )
    client.execute()  # raises CellExecutionError on any failing cell
    errors = [
        o for c in nb.cells if c.cell_type == "code" for o in c.outputs if o.output_type == "error"
    ]
    assert not errors
    assert (work / "oceansoda_dfco2.zarr").exists()
