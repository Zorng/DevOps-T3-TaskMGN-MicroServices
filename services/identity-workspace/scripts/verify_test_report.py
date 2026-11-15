"""Reject missing, empty, failed, or skipped pytest JUnit reports in CI."""

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


class ReportVerificationError(RuntimeError):
    pass


def verify_report(path: Path) -> int:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        raise ReportVerificationError("Test report is missing or invalid XML.") from None
    if root.tag not in {"testsuite", "testsuites"}:
        raise ReportVerificationError("Unsupported test report format.")
    cases = list(root.iter("testcase"))
    if not cases:
        raise ReportVerificationError("Test suite is unexpectedly empty.")
    if any(node.tag in {"failure", "error", "skipped"} for case in cases for node in case):
        raise ReportVerificationError("Test report contains failed, errored, or skipped tests.")
    try:
        unsuccessful = any(
            int(suite.get(key, "0")) != 0
            for suite in root.iter("testsuite")
            for key in ("failures", "errors", "skipped")
        )
    except ValueError:
        raise ReportVerificationError("Invalid test result counts.") from None
    if unsuccessful:
        raise ReportVerificationError("Test report summary contains unsuccessful tests.")
    return len(cases)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        count = verify_report(args.report)
    except ReportVerificationError as error:
        print(f"FAIL: {error}")
        return 1
    print(f"Verified {count} executed tests with zero failures, errors, or skips.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
