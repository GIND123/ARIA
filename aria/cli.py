"""Command line entry points.

The interface is the product; this exists for the few things that are better
done without one: verifying a packaged build, checking a workstation before
deployment, and verifying a bundle that has been copied somewhere.

    aria --self-test              run the self tests and report
    aria --system-check           check this workstation
    aria --verify-bundle PATH     verify a training bundle
    aria --verify-audit           verify the audit chain in the local database
    aria --version                print the version block
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .version import APP_LONG_NAME, APP_NAME, APP_VERSION, version_block


def _headless():
    """Create a Qt application without requiring a display.

    Some checks construct widgets, so a QApplication has to exist. The offscreen
    platform provides one on a build server with no desktop session.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.instance() or QApplication([])
    except Exception:
        return None


def run_self_test(as_json: bool = False) -> int:
    from .config import get_config
    from .platform.selftest import run_self_tests

    _headless()
    config = get_config()
    report = run_self_tests(config.paths)

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(report.to_text())
    return 0 if report.passed else 1


def run_system_check(as_json: bool = False) -> int:
    from .config import get_config
    from .platform.system_check import run_system_check as run_check

    _headless()
    config = get_config()
    report = run_check(config.paths, include_display=False)

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(report.to_text())
    return 0 if report.can_run else 1


def verify_bundle(path: str, as_json: bool = False) -> int:
    from .io.exporters.bundle import verify_bundle as verify

    report = verify(path)
    if as_json:
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["ok"] else 1

    name = Path(path).name
    if report["ok"]:
        manifest = report.get("manifest", {})
        print(f"{name} verified.")
        print(f"  {report['verified']} files checked, every checksum matched.")
        print(f"  Project: {manifest.get('project', 'unknown')}")
        print(f"  Cases: {manifest.get('n_cases', 'unknown')}")
        print(f"  Created: {manifest.get('generated_at', 'unknown')}")
        return 0

    print(f"{name} did not verify.")
    for issue in report["issues"]:
        print(f"  {issue}")
    for missing in report["missing"]:
        print(f"  missing: {missing}")
    for mismatched in report["mismatched"]:
        print(f"  checksum mismatch: {mismatched}")
    return 1


def verify_audit(as_json: bool = False) -> int:
    from .config import get_config
    from .store.db import open_database
    from .store.repository import Repository

    config = get_config()
    if not config.paths.database.exists():
        print(f"No database was found at {config.paths.database}.")
        return 1

    database = open_database(config.paths.database, read_only=True)
    try:
        repository = Repository(database)
        result = repository.verify_audit_chain()
        integrity = database.integrity_check()
    finally:
        database.close()

    if as_json:
        print(json.dumps({"chain": result, "storage": integrity}, indent=2, default=str))
        return 0 if result["valid"] and integrity["ok"] else 1

    if result["valid"]:
        print(f"The audit history is intact. {result['records_checked']} records checked.")
        print(f"  Head digest: {result.get('head_hash', '')[:32]}")
    else:
        print("The audit history is not intact.")
        print(f"  Breaks at record {result.get('broken_at_sequence')}")
        print(f"  {result['reason']}")

    print(f"Storage integrity: {'ok' if integrity['ok'] else 'failed'}")
    for name, value in integrity["checks"].items():
        print(f"  {name}: {value}")
    return 0 if result["valid"] and integrity["ok"] else 1


def print_version(as_json: bool = False) -> int:
    if as_json:
        print(json.dumps(version_block(), indent=2))
        return 0
    print(f"{APP_NAME} {APP_VERSION}")
    print(APP_LONG_NAME)
    print()
    for key, value in version_block().items():
        print(f"  {key.replace('_', ' '):28s} {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aria",
        description=(
            f"{APP_LONG_NAME}. Run without arguments to open the application."
        ),
        epilog=(
            "ARIA is an annotation and research data tool. It does not "
            "diagnose, it does not estimate bone mineral density, and it does "
            "not recommend treatment."
        ),
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help="run the built in self tests and report the result",
    )
    parser.add_argument(
        "--system-check", action="store_true",
        help="check whether this workstation meets the requirements",
    )
    parser.add_argument(
        "--verify-bundle", metavar="PATH",
        help="verify every checksum inside a training bundle",
    )
    parser.add_argument(
        "--verify-audit", action="store_true",
        help="verify the audit chain and storage integrity of the local database",
    )
    parser.add_argument(
        "--version", action="store_true", help="print the version block",
    )
    parser.add_argument(
        "--json", action="store_true", help="report as JSON rather than text",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args, _unknown = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    if args.version:
        return print_version(args.json)
    if args.self_test:
        return run_self_test(args.json)
    if args.system_check:
        return run_system_check(args.json)
    if args.verify_bundle:
        return verify_bundle(args.verify_bundle, args.json)
    if args.verify_audit:
        return verify_audit(args.json)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
