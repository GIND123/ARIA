"""Entry point for ``python -m aria`` and for the packaged executable.

With no arguments the application opens. With a recognised flag it runs that
check and exits, which is how a build pipeline verifies a packaged build before
anyone installs it.

Imports here are absolute rather than relative. A packaged build runs this file
as ``__main__`` with no parent package, so a relative import would fail at the
first line the moment the application left a development checkout.
"""

from __future__ import annotations

import sys

#: Flags handled without opening the interface.
HEADLESS_FLAGS = {
    "--self-test", "--system-check", "--verify-bundle", "--verify-audit",
    "--version", "-v", "--help", "-h",
}


def main() -> int:
    argv = sys.argv[1:]

    if any(arg.split("=")[0] in HEADLESS_FLAGS for arg in argv):
        from aria.cli import main as cli_main

        if "-v" in argv:
            argv = ["--version" if a == "-v" else a for a in argv]
        return cli_main(argv)

    from aria.app import run

    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
