"""The in application self tests, run under pytest as well.

The same functions back Help, Run diagnostics inside the application. Running
them here means the build pipeline and the installed application check exactly
the same things, so a pipeline pass cannot disagree with what a user sees.
"""

from __future__ import annotations

import pytest

from aria.platform.selftest import CHECKS, run_self_tests

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize(
    "key,name,group,function,matters",
    CHECKS,
    ids=[c[0] for c in CHECKS],
)
def test_self_check(key, name, group, function, matters, paths):
    """Each in application self test, as its own pytest case."""
    if key == "paths":
        passed, detail = function(paths)
    else:
        passed, detail = function()
    assert passed, f"{name} failed: {detail}. Why it matters: {matters}"
    assert detail, f"{name} passed without saying what it checked"


def test_report_is_complete_and_readable(paths):
    """The report the diagnostics dialog shows is well formed."""
    report = run_self_tests(paths)
    assert report.results
    assert len(report.results) == len(CHECKS)
    assert report.passed, report.summary()

    text = report.to_text()
    assert "ARIA self test report" in text
    for result in report.results:
        assert result.name in text
        assert result.group
        assert result.matters, f"{result.name} does not say why it matters"

    payload = report.to_dict()
    assert payload["n_failures"] == 0
    assert payload["n_tests"] == len(CHECKS)


def test_compatibility_report_is_well_formed(paths):
    """The compatibility check produces a complete, actionable report."""
    from aria.platform.system_check import CheckStatus, run_system_check

    report = run_system_check(paths, include_display=False, include_performance=False)
    assert report.results
    assert report.verdict()

    for result in report.results:
        assert result.name and result.category
        assert result.measured, f"{result.name} reported no measurement"
        if result.status_enum in (CheckStatus.FAIL, CheckStatus.WARN):
            assert result.remedy, f"{result.name} gave no next step"

    categories = {r.category for r in report.results}
    assert {"Runtime", "Hardware", "Storage", "Security"} <= categories


def test_startup_path_is_importable():
    """The application entry point imports without side effects."""
    import aria.__main__
    import aria.app

    assert callable(aria.app.run)
    assert callable(aria.__main__.main)
