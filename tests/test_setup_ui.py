"""Run the shipped JavaScript setup-flow regressions when Node.js is available."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("suite_name", ["setup_ui.test.cjs", "bank_evidence_ui.test.cjs"])
def test_setup_browser_logic_with_synthetic_local_api(suite_name):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional frontend regression checks require Node.js 18 or newer")
    suite = Path(__file__).with_name(suite_name)
    result = subprocess.run(
        [node, "--test", str(suite)], text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
