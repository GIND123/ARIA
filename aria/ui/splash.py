"""The window ARIA shows while it is opening.

Starting is not instantaneous. The database is opened and checked for
integrity, migrations are applied, a backup is taken and the interface is
built, and on a study of any size that is several seconds in which nothing is
on screen. A person who sees nothing assumes nothing happened and starts the
program again, which is how two copies end up holding the same file.

So the loader says what is happening, step by step, with the name of the
product beside it. It is deliberately plain: a mark, a name, a line of text
and a bar that fills as the real steps complete, rather than an animation that
would run at the same speed whether anything was working or not.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

from ..version import APP_LONG_NAME, APP_NAME, APP_VERSION
from .theme import PALETTE

#: Where the mark is read from, when one has been supplied. Replace this file
#: to change the mark the loader shows; nothing else has to be touched.
LOGO_NAME = "aria_logo.png"

WIDTH = 520
HEIGHT = 300
MARK = 112


def resources_dir() -> Path:
    """The folder resources are read from, running from source or packaged."""
    bundled = getattr(sys, "_MEIPASS", "")
    if bundled:
        candidate = Path(bundled) / "aria" / "resources"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent / "resources"


def logo_pixmap(size: int = MARK) -> QPixmap:
    """The mark, from the supplied file if there is one, drawn if there is not.

    Falling back to a drawn mark rather than to nothing means a build that was
    stripped of its resources still looks like a product rather than like a
    failure.
    """
    path = resources_dir() / LOGO_NAME
    if path.is_file():
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            # Scaled to twice the drawn size and then declared as such, so the
            # mark stays sharp on a high density display instead of being
            # stretched back up from its logical size.
            scaled = pixmap.scaled(
                size * 2, size * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            scaled.setDevicePixelRatio(2.0)
            return scaled
    return draw_mark(size)


def draw_mark(size: int = MARK, ratio: float = 2.0) -> QPixmap:
    """The mandibular curve with its measurement, painted at any size.

    The same shape as the application icon, without the tile behind it, so it
    sits on the loader's own background. ``ratio`` oversamples for a high
    density display; a file written to disk wants 1.0 and its own size.
    """
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size * ratio / 64.0, size * ratio / 64.0)

    curve = QPainterPath(QPointF(8, 20))
    curve.cubicTo(QPointF(10, 50), QPointF(54, 50), QPointF(56, 20))
    p.setPen(QPen(QColor(PALETTE.accent), 4.2, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawPath(curve)

    inner = QPainterPath(QPointF(16, 20))
    inner.cubicTo(QPointF(18, 41), QPointF(46, 41), QPointF(48, 20))
    p.setPen(QPen(QColor(PALETTE.text_dim), 2.4, Qt.DashLine))
    p.drawPath(inner)

    p.setPen(QPen(QColor("#E69F00"), 3.0, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(32, 28), QPointF(32, 46))
    p.setBrush(QColor("#E69F00"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(32, 23), 3.6, 3.6)
    p.end()
    return pixmap


class SplashScreen(QWidget):
    """Shown from the moment ARIA starts until its window is ready."""

    def __init__(self, total_steps: int = 6):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(WIDTH, HEIGHT)

        self._mark = logo_pixmap()
        self._message = "Starting"
        self._step = 0
        self._total = max(1, total_steps)

        screen = QApplication.primaryScreen()
        if screen is not None:
            centre = screen.availableGeometry().center()
            self.move(centre.x() - WIDTH // 2, centre.y() - HEIGHT // 2)

    # -- progress ------------------------------------------------------------

    def step(self, message: str) -> None:
        """Say what is being done, and advance the bar by one real step."""
        self._message = message
        self._step = min(self._step + 1, self._total)
        self.repaint()
        QApplication.processEvents()

    def finish(self, window=None) -> None:
        self.close()
        if window is not None:
            window.activateWindow()
            window.raise_()

    # -- painting ------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        card = QRectF(0.5, 0.5, WIDTH - 1, HEIGHT - 1)
        p.setBrush(QColor(PALETTE.panel))
        p.setPen(QPen(QColor(PALETTE.border_light), 1))
        p.drawRoundedRect(card, 10, 10)

        mark_x = (WIDTH - MARK) / 2
        p.drawPixmap(int(mark_x), 34, self._mark)

        title = QFont(self.font())
        title.setPointSizeF(max(22.0, self.font().pointSizeF() + 13))
        title.setBold(True)
        title.setLetterSpacing(QFont.AbsoluteSpacing, 5)
        p.setFont(title)
        p.setPen(QColor(PALETTE.text))
        p.drawText(
            QRectF(0, MARK + 44, WIDTH, 44), Qt.AlignHCenter | Qt.AlignVCenter, APP_NAME
        )

        subtitle = QFont(self.font())
        subtitle.setPointSizeF(max(8.5, self.font().pointSizeF() - 0.5))
        p.setFont(subtitle)
        p.setPen(QColor(PALETTE.text_dim))
        p.drawText(
            QRectF(20, MARK + 86, WIDTH - 40, 20),
            Qt.AlignHCenter | Qt.AlignVCenter,
            APP_LONG_NAME.split(":", 1)[-1].strip(),
        )

        bar = QRectF(60, HEIGHT - 52, WIDTH - 120, 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(PALETTE.border_light))
        p.drawRoundedRect(bar, 2, 2)
        filled = QRectF(bar)
        filled.setWidth(bar.width() * self._step / self._total)
        p.setBrush(QColor(PALETTE.accent))
        p.drawRoundedRect(filled, 2, 2)

        p.setFont(subtitle)
        p.setPen(QColor(PALETTE.text_dim))
        p.drawText(
            QRectF(20, HEIGHT - 40, WIDTH - 40, 18),
            Qt.AlignHCenter | Qt.AlignVCenter,
            self._message,
        )
        p.setPen(QColor(PALETTE.text_disabled))
        p.drawText(
            QRectF(20, HEIGHT - 24, WIDTH - 40, 16),
            Qt.AlignHCenter | Qt.AlignVCenter,
            f"Version {APP_VERSION}   Runs entirely on this workstation",
        )
        p.end()
