"""Command line entry points.

The interface is the product; this exists for the few things that are better
done without one: verifying a packaged build, checking a workstation before
deployment, verifying a bundle that has been copied somewhere, and getting back
into a study whose only administrator has forgotten the password.

    aria --self-test              run the self tests and report
    aria --system-check           check this workstation
    aria --verify-bundle PATH     verify a training bundle
    aria --verify-audit           verify the audit chain in the local database
    aria --reset-password [NAME]  reset a forgotten password, or list accounts
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


def _report(text: str) -> None:
    """Print, or show a message box when there is nowhere to print to.

    A packaged build is a windowed program, so run from a shortcut or the Run
    box it has no console, and a temporary password printed into nothing is a
    reset nobody can use.
    """
    if sys.stdout is not None:
        print(text)
        return
    from PySide6.QtWidgets import QApplication, QMessageBox

    _app = QApplication.instance() or QApplication([])
    QMessageBox.information(None, f"{APP_NAME} password reset", text)


def _computer_account() -> str:
    import getpass

    try:
        return getpass.getuser()
    except Exception:
        return "an unnamed computer account"


def workstation_reset_command(username: str = "") -> str:
    """The command that resets a password on this workstation, ready to paste."""
    if getattr(sys, "frozen", False):
        parts = [sys.executable]
    else:
        parts = [sys.executable, "-m", "aria"]
    parts += ["--reset-password", username or "USERNAME"]
    if sys.platform.startswith("win"):
        import subprocess

        return subprocess.list2cmdline(parts)
    import shlex

    return shlex.join(parts)


def reset_password(username: str, as_json: bool = False) -> int:
    """Reset a forgotten password, or with no name list the accounts.

    Whoever can run this can already open the database file, so it asks for no
    proof. It records the reset, with the computer account that made it, and
    the account has to choose a new password at its next sign in.
    """
    from .config import get_config
    from .core.models import Role
    from .security.auth import AuthService
    from .store.db import open_database
    from .store.repository import Repository

    config = get_config()
    if not config.paths.database.exists():
        _report(f"No database was found at {config.paths.database}.")
        return 1

    def listing(users) -> str:
        width = max([len(u.username) for u in users] + [8])
        lines = [f"Accounts in {config.paths.database}:", ""]
        for u in users:
            lines.append(
                f"  {u.username:<{width}}  {u.display_name:<24}  "
                f"{Role.DISPLAY.get(u.role, u.role):<14}  "
                f"{'active' if u.active else 'deactivated'}"
            )
        lines += [
            "",
            "Run again with the username to reset its password, for example:",
            f"  {workstation_reset_command(users[0].username if users else '')}",
        ]
        return "\n".join(lines)

    if not username:
        # Read only, so listing the accounts changes nothing, not even the
        # schema version of a file an earlier build wrote.
        database = open_database(config.paths.database, read_only=True)
        try:
            users = Repository(database).list_users(include_inactive=True)
        finally:
            database.close()
        if as_json:
            print(json.dumps({"accounts": [
                {"username": u.username, "display_name": u.display_name,
                 "role": u.role, "active": u.active}
                for u in users
            ]}, indent=2))
        else:
            _report(listing(users))
        return 0

    operator = _computer_account()
    database = open_database(config.paths.database)
    try:
        repository = Repository(database)
        try:
            user, temporary = AuthService(repository, config.settings).reset_password_at_workstation(
                username, operator
            )
        except LookupError as exc:
            users = repository.list_users(include_inactive=True)
            if as_json:
                print(json.dumps({"reset": False, "reason": str(exc)}, indent=2))
            else:
                _report(f"{exc}\n\n{listing(users)}")
            return 1
    finally:
        database.close()

    if as_json:
        print(json.dumps({
            "reset": True, "username": user.username,
            "temporary_password": temporary, "must_change_password": True,
            "active": user.active,
        }, indent=2))
        return 0

    lines = [
        f"The password for {user.username} has been reset.",
        "",
        f"  Temporary password: {temporary}",
        "",
        "Sign in with it, and ARIA asks for a new password straight away.",
        f"The reset is recorded in the audit history, made by the computer "
        f"account {operator}.",
    ]
    if not user.active:
        lines += [
            "",
            "This account is deactivated, so it still cannot sign in. Another "
            "administrator can reactivate it in Administration, Accounts.",
        ]
    _report("\n".join(lines))
    return 0


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
        "--reset-password", metavar="USERNAME", nargs="?", const="",
        help=(
            "set a temporary password for a forgotten one, to be changed at the "
            "next sign in; with no username, list the accounts"
        ),
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
    if args.reset_password is not None:
        return reset_password(args.reset_password, args.json)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
