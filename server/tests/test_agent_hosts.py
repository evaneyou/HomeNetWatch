"""The Pi agent's host check: a target can never reach ping or mtr as an option."""

import importlib.util
from pathlib import Path

import pytest


def _checks():
    here = Path(__file__).resolve()
    candidates = [here.parents[2] / "monitor" / "checks.py", Path("/monitor/checks.py")]
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        pytest.skip("monitor/checks.py is not in this image")
    spec = importlib.util.spec_from_file_location("monitor_checks_hosts", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("host", ["1.1.1.1", "example.com", "10.7.0.1", "a", "9to5.example"])
def test_ordinary_hosts_are_accepted(host):
    assert _checks()._valid_host(host)


@pytest.mark.parametrize("host", ["--report-wide", "-c100", "-", ".example.com", "a..b", "", "a b", "x" * 254])
def test_option_like_or_malformed_hosts_are_refused(host):
    assert not _checks()._valid_host(host)
