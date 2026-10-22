"""Write the mark ARIA ships with.

The loader, the window icon and the installer all show the same mark. It is
drawn here rather than kept only as a binary, so that it can be regenerated at
any size and so that a change to it is a change to readable source rather than
to an opaque file.

Run it after changing the shape:

    python scripts/make_logo.py --force

To use artwork of your own instead, put it at ``aria/resources/aria_logo.png``
with a transparent background, square, at least 512 pixels across. The loader
reads that file when it is there and falls back to the drawn mark when it is
not, so nothing else has to change. A build runs this script and it keeps a
mark that is already there, so artwork put in deliberately survives a build;
``--force`` is what replaces it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "aria" / "resources" / "aria_logo.png"
ICON_PNG = ROOT / "packaging" / "aria.png"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1024, help="pixels square")
    parser.add_argument("--output", default=str(OUTPUT))
    parser.add_argument(
        "--force", action="store_true",
        help=(
            "overwrite a mark that is already there, which is what you want "
            "after changing the drawing and never what you want on a build "
            "machine holding somebody's own artwork"
        ),
    )
    args = parser.parse_args(argv)

    existing = Path(args.output)
    if existing.is_file() and not args.force:
        # A build runs this script, and a build must not destroy artwork
        # somebody put here deliberately. Pass --force after changing the
        # drawing below.
        print(f"Keeping the mark already at {existing}")
        return 0

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])

    from aria.ui.splash import draw_mark

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    pixmap = draw_mark(args.size, ratio=1.0)
    if not pixmap.save(str(output), "PNG"):
        print(f"The mark could not be written to {output}", file=sys.stderr)
        return 1

    print(f"Wrote {output} at {pixmap.width()} by {pixmap.height()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
