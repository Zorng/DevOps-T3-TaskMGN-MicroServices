import importlib.util
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_test_report", SERVICE_ROOT / "scripts/verify_test_report.py")
reports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reports)


def test_nonempty_successful_junit_report_is_accepted(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text('<testsuites><testsuite tests="2"><testcase name="one"/><testcase name="two"/></testsuite></testsuites>')
    assert reports.verify_report(report) == 2


@pytest.mark.parametrize("content", [
    '<testsuites><testsuite tests="0"/></testsuites>',
    '<testsuite><testcase name="one"><failure>synthetic failure</failure></testcase></testsuite>',
    '<testsuite><testcase name="collection"><error>synthetic error</error></testcase></testsuite>',
    '<testsuite><testcase name="one"><skipped/></testcase></testsuite>',
    '<testsuite><testcase name="pass"/><testcase name="skip"><skipped/></testcase></testsuite>',
    '<testsuite errors="1"><testcase name="one"/></testsuite>',
    '<testsuite skipped="1"><testcase name="one"/></testsuite>',
    '<testsuite failures="invalid"><testcase name="one"/></testsuite>',
    '<unexpected><testcase name="one"/></unexpected>',
    'not XML',
])
def test_unsuccessful_empty_or_invalid_report_is_rejected(tmp_path, content):
    report = tmp_path / "report.xml"
    report.write_text(content)
    with pytest.raises(reports.ReportVerificationError):
        reports.verify_report(report)


def test_missing_report_fails_without_exposing_file_contents(tmp_path):
    with pytest.raises(reports.ReportVerificationError, match="missing or invalid XML"):
        reports.verify_report(tmp_path / "missing.xml")
