"""Write the third party licence notice that ships with ARIA.

Run as part of a packaged build. The notice lists only what the bundle
actually contains, which is not the same as what happens to be installed in
the build environment: a plain dump of the environment lists tools that were
used to build the product and never shipped with it, and omitting the ones
that are shipped is the more serious error of the two.

PySide6 and shiboken6 are used under the LGPL, which asks for a notice and for
the recipient to be able to replace the library. Qt is dynamically linked here
rather than statically combined, so a recipient can substitute their own build
of the same version, and the notice says so.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: What the application imports at run time. Kept here rather than read from
#: the environment so that a build machine with extra packages installed
#: produces the same notice as a clean one.
SHIPPED = (
    "PySide6",
    "PySide6-Essentials",
    "PySide6-Addons",
    "shiboken6",
    "pydicom",
    "numpy",
    "Pillow",
    "psutil",
    "cryptography",
)

PREAMBLE = """Third party components distributed with ARIA
===========================================

ARIA is built on the components below. Each is distributed under its own
licence, and the full text of each licence is supplied by its project at the
address given. None of them is modified.

PySide6 and shiboken6 are used under the LGPL. The Qt libraries they wrap are
dynamically linked rather than statically combined with ARIA, so a recipient
may replace them with their own build of the same version. Their sources are
available from the project address below.
"""

POSTAMBLE = """The DICOM standard is published by NEMA. ARIA reads and writes DICOM objects
but is not endorsed by, nor affiliated with, NEMA.

ARIA's own licence is in LICENSE.txt beside this file.
"""

COLUMN = 26


def _licence_of(meta) -> str:
    """The licence, from whichever field the package chose to put it in."""
    declared = (meta.get("License") or "").strip()
    if declared and len(declared) <= 60 and "\n" not in declared:
        return declared
    classifiers = [
        c for c in (meta.get_all("Classifier") or []) if c.startswith("License")
    ]
    if classifiers:
        return classifiers[0].split("::")[-1].strip()
    expression = (meta.get("License-Expression") or "").strip()
    return expression or "see the project"


def _url_of(meta) -> str:
    home = (meta.get("Home-page") or "").strip()
    if home:
        return home
    for entry in meta.get_all("Project-URL") or []:
        if "," in entry:
            return entry.split(",", 1)[1].strip()
    return ""


def build_notice(packages=SHIPPED) -> tuple:
    """Return the notice text and the names that could not be found."""
    lines = [PREAMBLE]
    missing: list = []
    for name in packages:
        try:
            meta = metadata.metadata(name)
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        lines.append(f"{name:<{COLUMN}} {version:<10} {_licence_of(meta)}")
        url = _url_of(meta)
        if url:
            lines.append(f"{'':<{COLUMN}} {url}")
        lines.append("")
    lines.append(POSTAMBLE)
    return "\n".join(lines), missing


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default=str(ROOT / "third_party_licences.txt"),
        help="where to write the notice",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help=(
            "fail when a shipped package cannot be found, which is what a "
            "release build wants: a notice missing a component it ships is "
            "worse than no build at all"
        ),
    )
    args = parser.parse_args(argv)

    notice, missing = build_notice()
    Path(args.output).write_text(notice, encoding="utf-8")
    print(f"Wrote {args.output}")

    if missing:
        print(
            "These are listed as shipped but are not installed here: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        if args.strict:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
