"""Generate the platform icon files from the application's vector icon.

The application draws its icon at runtime, but an installer and a desktop entry
need real files. This renders the same artwork to the formats each platform
expects, so the icon in the taskbar, the installer and the application are the
same drawing rather than three that drifted apart.

Outputs
-------
``packaging/aria.ico``      Windows, multiple sizes in one file
``packaging/aria.png``      generic 512 pixel image
``packaging/aria.iconset/`` macOS source folder for iconutil
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Sizes Windows uses in the shell, the task bar and the alt tab switcher.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: The macOS iconset naming convention, which iconutil requires exactly.
ICONSET = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)


def render(size: int):
    """Render the application icon at one size as a Pillow image.

    Pixel data is copied out of the QImage directly rather than going through
    Qt's PNG writer, which is not available under every platform plugin and
    would make this script depend on where it is run.
    """
    import numpy as np
    from PIL import Image
    from PySide6.QtGui import QImage

    from aria.ui.icons import application_icon

    image = application_icon(size).pixmap(size, size).toImage()
    image = image.convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()

    pointer = image.constBits()
    stride = image.bytesPerLine()
    raw = np.frombuffer(bytes(pointer)[: stride * height], dtype=np.uint8)
    raw = raw.reshape(height, stride)[:, : width * 4].reshape(height, width, 4)
    return Image.fromarray(raw.copy())


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    out = ROOT / "packaging"
    out.mkdir(parents=True, exist_ok=True)

    largest = render(1024)
    largest.resize((512, 512)).save(out / "aria.png")
    print(f"  {out / 'aria.png'}")

    images = [render(size) for size in ICO_SIZES]
    images[-1].save(
        out / "aria.ico", format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=images[:-1],
    )
    print(f"  {out / 'aria.ico'}")

    iconset = out / "aria.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for name, size in ICONSET:
        render(size).save(iconset / name)
    print(f"  {iconset}  ({len(ICONSET)} files)")

    # Pillow can write ICNS directly on some platforms. Where it cannot, the
    # macOS build script runs iconutil against the iconset folder instead.
    try:
        largest.save(out / "aria.icns", format="ICNS")
        print(f"  {out / 'aria.icns'}")
    except Exception as exc:
        print(
            f"  aria.icns was not written here ({exc}). "
            f"The macOS build script produces it from aria.iconset with iconutil."
        )

    from aria.ui.icons import clear_cache

    clear_cache()
    return 0


if __name__ == "__main__":
    print("Writing icon files:")
    sys.exit(main())
