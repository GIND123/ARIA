"""Vector icons drawn at runtime.

Icons are painted rather than loaded from files. That keeps the installation
free of binary assets, gives crisp results at any display scaling without
shipping several sizes, and lets an icon take its colour from the theme so the
high contrast palette works everywhere without a second icon set.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .theme import PALETTE

#: Logical size icons are drawn at. The painter scales to the requested size.
BASE = 24


def _pen(colour: QColor, width: float = 2.0) -> QPen:
    pen = QPen(colour, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


# ---------------------------------------------------------------------------
# Individual icon painters. Each draws inside a 24 by 24 box.
# ---------------------------------------------------------------------------


def _open(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawPolyline([QPointF(3, 19), QPointF(3, 6), QPointF(10, 6), QPointF(12, 9), QPointF(20, 9)])
    p.drawPolyline([QPointF(3, 19), QPointF(6, 12), QPointF(22, 12), QPointF(19, 19), QPointF(3, 19)])


def _save(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawPolyline([QPointF(4, 4), QPointF(17, 4), QPointF(20, 7), QPointF(20, 20), QPointF(4, 20), QPointF(4, 4)])
    p.drawRect(QRectF(8, 4, 8, 6))
    p.drawRect(QRectF(7, 13, 10, 7))


def _import(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawLine(QPointF(12, 3), QPointF(12, 14))
    p.drawPolyline([QPointF(8, 10), QPointF(12, 14), QPointF(16, 10)])
    p.drawPolyline([QPointF(4, 16), QPointF(4, 20), QPointF(20, 20), QPointF(20, 16)])


def _export(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawLine(QPointF(12, 14), QPointF(12, 3))
    p.drawPolyline([QPointF(8, 7), QPointF(12, 3), QPointF(16, 7)])
    p.drawPolyline([QPointF(4, 16), QPointF(4, 20), QPointF(20, 20), QPointF(20, 16)])


def _bundle(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawPolyline([QPointF(3, 8), QPointF(12, 3), QPointF(21, 8), QPointF(21, 17), QPointF(12, 22), QPointF(3, 17), QPointF(3, 8)])
    p.drawLine(QPointF(3, 8), QPointF(12, 13))
    p.drawLine(QPointF(21, 8), QPointF(12, 13))
    p.drawLine(QPointF(12, 13), QPointF(12, 22))


def _cursor(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.8))
    path = QPainterPath(QPointF(6, 3))
    path.lineTo(6, 18)
    path.lineTo(10, 14)
    path.lineTo(13, 20)
    path.lineTo(16, 19)
    path.lineTo(13, 13)
    path.lineTo(18, 12)
    path.closeSubpath()
    p.drawPath(path)


def _point(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawLine(QPointF(12, 3), QPointF(12, 8))
    p.drawLine(QPointF(12, 16), QPointF(12, 21))
    p.drawLine(QPointF(3, 12), QPointF(8, 12))
    p.drawLine(QPointF(16, 12), QPointF(21, 12))
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 12), 3.0, 3.0)


def _line(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawLine(QPointF(5, 19), QPointF(19, 5))
    p.setBrush(c)
    p.drawEllipse(QPointF(5, 19), 2.6, 2.6)
    p.drawEllipse(QPointF(19, 5), 2.6, 2.6)


def _polyline(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawPolyline([QPointF(3, 17), QPointF(9, 8), QPointF(15, 15), QPointF(21, 5)])
    p.setBrush(c)
    for pt in (QPointF(3, 17), QPointF(9, 8), QPointF(15, 15), QPointF(21, 5)):
        p.drawEllipse(pt, 2.0, 2.0)


def _polygon(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    pts = [QPointF(12, 3), QPointF(21, 10), QPointF(18, 20), QPointF(6, 20), QPointF(3, 10)]
    p.drawPolygon(pts)
    p.setBrush(c)
    for pt in pts:
        p.drawEllipse(pt, 1.8, 1.8)


def _box(p: QPainter, c: QColor) -> None:
    pen = _pen(c)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawRect(QRectF(4, 6, 16, 12))
    p.setPen(_pen(c))
    p.setBrush(c)
    for pt in (QPointF(4, 6), QPointF(20, 6), QPointF(20, 18), QPointF(4, 18)):
        p.drawRect(QRectF(pt.x() - 1.8, pt.y() - 1.8, 3.6, 3.6))


def _roi(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawRect(QRectF(5, 5, 14, 14))
    pen = _pen(c, 1.0)
    pen.setStyle(Qt.DotLine)
    p.setPen(pen)
    for i in range(1, 4):
        p.drawLine(QPointF(5, 5 + i * 3.5), QPointF(19, 5 + i * 3.5))
        p.drawLine(QPointF(5 + i * 3.5, 5), QPointF(5 + i * 3.5, 19))


def _brush(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawPolyline([QPointF(6, 18), QPointF(6, 13), QPointF(16, 3), QPointF(21, 8), QPointF(11, 18), QPointF(6, 18)])
    p.drawLine(QPointF(3, 21), QPointF(8, 18))


def _eraser(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawPolyline([QPointF(4, 17), QPointF(13, 4), QPointF(20, 9), QPointF(11, 21), QPointF(4, 17)])
    p.drawLine(QPointF(11, 21), QPointF(21, 21))


def _zoom(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(10.5, 10.5), 6.5, 6.5)
    p.drawLine(QPointF(15.5, 15.5), QPointF(21, 21))


def _pan(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.8))
    p.drawLine(QPointF(12, 3), QPointF(12, 21))
    p.drawLine(QPointF(3, 12), QPointF(21, 12))
    for a, b, d in ((12, 3, "u"), (12, 21, "d"), (3, 12, "l"), (21, 12, "r")):
        if d == "u":
            p.drawPolyline([QPointF(9, 6), QPointF(12, 3), QPointF(15, 6)])
        elif d == "d":
            p.drawPolyline([QPointF(9, 18), QPointF(12, 21), QPointF(15, 18)])
        elif d == "l":
            p.drawPolyline([QPointF(6, 9), QPointF(3, 12), QPointF(6, 15)])
        else:
            p.drawPolyline([QPointF(18, 9), QPointF(21, 12), QPointF(18, 15)])


def _fit(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawRect(QRectF(3, 5, 18, 14))
    p.drawPolyline([QPointF(7, 9), QPointF(7, 12), QPointF(10, 12)])
    p.drawPolyline([QPointF(17, 15), QPointF(17, 12), QPointF(14, 12)])


def _reset(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    rect = QRectF(4, 4, 16, 16)
    p.drawArc(rect, 40 * 16, 280 * 16)
    p.drawPolyline([QPointF(17, 3), QPointF(18.5, 8), QPointF(13.5, 8.5)])


def _window_level(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    path = QPainterPath()
    path.moveTo(12, 3.5)
    path.arcTo(QRectF(3.5, 3.5, 17, 17), 90, -180)
    path.closeSubpath()
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawPath(path)


def _invert(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawRect(QRectF(4, 4, 16, 16))
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawRect(QRectF(12, 4, 8, 16))


def _contrast(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawLine(QPointF(12, 3.5), QPointF(12, 20.5))
    p.setPen(_pen(c, 1.0))
    for i in range(1, 6):
        y = 5 + i * 2.6
        half = math.sqrt(max(0.0, 72.25 - (y - 12) ** 2))
        p.drawLine(QPointF(12, y), QPointF(12 + half, y))


def _filter(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawPolyline([QPointF(3, 5), QPointF(21, 5), QPointF(14, 13), QPointF(14, 20), QPointF(10, 18), QPointF(10, 13), QPointF(3, 5)])


def _undo(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawArc(QRectF(5, 7, 15, 13), 30 * 16, 150 * 16)
    p.drawPolyline([QPointF(4, 6), QPointF(5.5, 11), QPointF(10.5, 10)])


def _redo(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawArc(QRectF(4, 7, 15, 13), 0 * 16, 150 * 16)
    p.drawPolyline([QPointF(20, 6), QPointF(18.5, 11), QPointF(13.5, 10)])


def _delete(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawLine(QPointF(4, 6), QPointF(20, 6))
    p.drawPolyline([QPointF(6, 6), QPointF(7, 21), QPointF(17, 21), QPointF(18, 6)])
    p.drawLine(QPointF(9, 3), QPointF(15, 3))
    p.drawLine(QPointF(10, 10), QPointF(10, 17))
    p.drawLine(QPointF(14, 10), QPointF(14, 17))


def _visible(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    path = QPainterPath(QPointF(2, 12))
    path.quadTo(QPointF(12, 3), QPointF(22, 12))
    path.quadTo(QPointF(12, 21), QPointF(2, 12))
    p.drawPath(path)
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)


def _hidden(p: QPainter, c: QColor) -> None:
    _visible(p, c)
    p.setPen(_pen(c, 1.8))
    p.drawLine(QPointF(4, 20), QPointF(20, 4))


def _lock(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.7))
    p.drawRect(QRectF(5, 11, 14, 10))
    p.drawArc(QRectF(8, 4, 8, 11), 0, 180 * 16)


def _check(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 2.4))
    p.drawPolyline([QPointF(4, 12), QPointF(10, 18), QPointF(20, 6)])


def _warning(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawPolyline([QPointF(12, 3), QPointF(22, 20), QPointF(2, 20), QPointF(12, 3)])
    p.drawLine(QPointF(12, 9), QPointF(12, 15))
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 17.6), 1.1, 1.1)


def _error(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(12, 12), 9.0, 9.0)
    p.drawLine(QPointF(8, 8), QPointF(16, 16))
    p.drawLine(QPointF(16, 8), QPointF(8, 16))


def _info(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(12, 12), 9.0, 9.0)
    p.drawLine(QPointF(12, 11), QPointF(12, 17))
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 7.6), 1.1, 1.1)


def _help(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c))
    p.drawEllipse(QPointF(12, 12), 9.0, 9.0)
    path = QPainterPath(QPointF(9, 9.5))
    path.cubicTo(QPointF(9.5, 6), QPointF(15, 6.5), QPointF(14, 10))
    path.cubicTo(QPointF(13.4, 12), QPointF(12, 12.4), QPointF(12, 15))
    p.drawPath(path)
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 17.6), 1.1, 1.1)


def _settings(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawEllipse(QPointF(12, 12), 3.4, 3.4)
    for i in range(8):
        angle = i * math.pi / 4
        x0 = 12 + math.cos(angle) * 6.0
        y0 = 12 + math.sin(angle) * 6.0
        x1 = 12 + math.cos(angle) * 9.2
        y1 = 12 + math.sin(angle) * 9.2
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))


def _user(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.7))
    p.drawEllipse(QPointF(12, 8), 4.2, 4.2)
    path = QPainterPath(QPointF(4, 21))
    path.quadTo(QPointF(12, 13), QPointF(20, 21))
    p.drawPath(path)


def _users(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.5))
    p.drawEllipse(QPointF(9, 8), 3.6, 3.6)
    path = QPainterPath(QPointF(2, 20))
    path.quadTo(QPointF(9, 13), QPointF(16, 20))
    p.drawPath(path)
    p.drawArc(QRectF(13, 5, 7, 7), -90 * 16, 180 * 16)
    p.drawArc(QRectF(14, 14, 9, 8), 0, 180 * 16)


def _review(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawRect(QRectF(4, 3, 13, 18))
    p.drawLine(QPointF(7, 8), QPointF(13, 8))
    p.drawLine(QPointF(7, 12), QPointF(13, 12))
    p.setPen(_pen(c, 2.2))
    p.drawPolyline([QPointF(12, 17), QPointF(15, 20), QPointF(21, 12)])


def _audit(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawRect(QRectF(4, 3, 16, 18))
    for y in (8, 12, 16):
        p.drawLine(QPointF(7, y), QPointF(17, y))
    p.setBrush(c)
    for y in (8, 12, 16):
        p.drawEllipse(QPointF(6, y), 0.9, 0.9)


def _measure(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.save()
    p.translate(12, 12)
    p.rotate(-45)
    p.drawRect(QRectF(-11, -4, 22, 8))
    for i in range(1, 5):
        x = -11 + i * 4.4
        p.drawLine(QPointF(x, -4), QPointF(x, -4 + (4 if i % 2 == 0 else 2.4)))
    p.restore()


def _chart(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.7))
    p.drawPolyline([QPointF(3, 3), QPointF(3, 21), QPointF(21, 21)])
    p.drawPolyline([QPointF(6, 16), QPointF(11, 10), QPointF(15, 14), QPointF(20, 6)])


def _grid(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.5))
    p.drawRect(QRectF(3, 3, 18, 18))
    p.drawLine(QPointF(12, 3), QPointF(12, 21))
    p.drawLine(QPointF(3, 12), QPointF(21, 12))


def _one_up(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.5))
    p.drawRect(QRectF(3, 4, 18, 16))


def _side_by_side(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.5))
    p.drawRect(QRectF(3, 4, 8.5, 16))
    p.drawRect(QRectF(12.5, 4, 8.5, 16))


def _calibrate(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawLine(QPointF(3, 17), QPointF(21, 17))
    for i in range(5):
        x = 3 + i * 4.5
        p.drawLine(QPointF(x, 17), QPointF(x, 17 - (7 if i % 2 == 0 else 4)))
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 20.5), 1.4, 1.4)


def _flag(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.7))
    p.drawLine(QPointF(5, 3), QPointF(5, 21))
    p.drawPolyline([QPointF(5, 4), QPointF(19, 7), QPointF(5, 12)])


def _grade(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawRect(QRectF(3, 6, 18, 12))
    p.drawLine(QPointF(9, 6), QPointF(9, 18))
    p.drawLine(QPointF(15, 6), QPointF(15, 18))


def _texture(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.3))
    p.drawRect(QRectF(3, 3, 18, 18))
    for i in range(6):
        p.drawLine(QPointF(3 + i * 3, 3), QPointF(3, 3 + i * 3))
        p.drawLine(QPointF(21 - i * 3, 21), QPointF(21, 21 - i * 3))


def _snap(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawLine(QPointF(3, 18), QPointF(21, 6))
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 12), 3.0, 3.0)
    pen = _pen(c, 1.0)
    pen.setStyle(Qt.DotLine)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(12, 12), 6.5, 6.5)


def _construct(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.5))
    p.drawLine(QPointF(3, 20), QPointF(20, 20))
    pen = _pen(c, 1.2)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawLine(QPointF(11, 20), QPointF(11, 4))
    p.setPen(_pen(c, 1.5))
    p.drawLine(QPointF(8, 17), QPointF(11, 17))
    p.setBrush(c)
    p.drawEllipse(QPointF(11, 8), 2.4, 2.4)


def _tour(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawEllipse(QPointF(12, 12), 9.0, 9.0)
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 12), 2.6, 2.6)
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(12, 1.5), QPointF(12, 5))
    p.drawLine(QPointF(12, 19), QPointF(12, 22.5))
    p.drawLine(QPointF(1.5, 12), QPointF(5, 12))
    p.drawLine(QPointF(19, 12), QPointF(22.5, 12))


def _diagnostics(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawPolyline([QPointF(2, 13), QPointF(7, 13), QPointF(9.5, 6), QPointF(13, 20), QPointF(15.5, 13), QPointF(22, 13)])


def _search(p: QPainter, c: QColor) -> None:
    _zoom(p, c)


def _refresh(p: QPainter, c: QColor) -> None:
    _reset(p, c)


def _back(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 2.0))
    p.drawLine(QPointF(20, 12), QPointF(5, 12))
    p.drawPolyline([QPointF(11, 6), QPointF(5, 12), QPointF(11, 18)])


def _forward(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 2.0))
    p.drawLine(QPointF(4, 12), QPointF(19, 12))
    p.drawPolyline([QPointF(13, 6), QPointF(19, 12), QPointF(13, 18)])


def _submit(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.7))
    p.drawPolyline([QPointF(3, 12), QPointF(21, 4), QPointF(14, 21), QPointF(11, 14), QPointF(3, 12)])
    p.drawLine(QPointF(11, 14), QPointF(21, 4))


def _folder(p: QPainter, c: QColor) -> None:
    _open(p, c)


def _image(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawRect(QRectF(3, 5, 18, 14))
    p.drawPolyline([QPointF(3, 16), QPointF(9, 10), QPointF(14, 15), QPointF(17, 12), QPointF(21, 16)])
    p.setBrush(c)
    p.drawEllipse(QPointF(8, 9), 1.5, 1.5)


def _copy(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawRect(QRectF(8, 8, 13, 13))
    p.drawPolyline([QPointF(5, 16), QPointF(3, 16), QPointF(3, 3), QPointF(16, 3), QPointF(16, 5)])


def _print(p: QPainter, c: QColor) -> None:
    p.setPen(_pen(c, 1.6))
    p.drawPolyline([QPointF(7, 8), QPointF(7, 3), QPointF(17, 3), QPointF(17, 8)])
    p.drawRect(QRectF(3, 8, 18, 8))
    p.drawRect(QRectF(7, 14, 10, 7))


PAINTERS = {
    "open": _open, "save": _save, "import": _import, "export": _export,
    "bundle": _bundle, "cursor": _cursor, "point": _point, "line": _line,
    "polyline": _polyline, "polygon": _polygon, "box": _box, "roi": _roi,
    "brush": _brush, "eraser": _eraser, "zoom": _zoom, "pan": _pan, "fit": _fit,
    "reset": _reset, "window_level": _window_level, "invert": _invert,
    "contrast": _contrast, "filter": _filter, "undo": _undo, "redo": _redo,
    "delete": _delete, "visible": _visible, "hidden": _hidden, "lock": _lock,
    "check": _check, "warning": _warning, "error": _error, "info": _info,
    "help": _help, "settings": _settings, "user": _user, "users": _users,
    "review": _review, "audit": _audit, "measure": _measure, "chart": _chart,
    "grid": _grid, "one_up": _one_up, "side_by_side": _side_by_side,
    "calibrate": _calibrate, "flag": _flag, "grade": _grade, "texture": _texture,
    "snap": _snap, "construct": _construct, "tour": _tour,
    "diagnostics": _diagnostics, "search": _search, "refresh": _refresh,
    "back": _back, "forward": _forward, "submit": _submit, "folder": _folder,
    "image": _image, "copy": _copy, "print": _print,
}

_CACHE: dict = {}


def pixmap(name: str, size: int = 20, colour: str | None = None, ratio: float = 1.0) -> QPixmap:
    """Render one icon to a pixmap."""
    key = (name, size, colour, round(ratio, 2))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    physical = max(1, int(round(size * ratio)))
    pm = QPixmap(physical, physical)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    scale = physical / BASE
    painter.scale(scale, scale)
    painter.setBrush(Qt.NoBrush)
    fn = PAINTERS.get(name)
    if fn is not None:
        fn(painter, QColor(colour or PALETTE.text_dim))
    painter.end()

    _CACHE[key] = pm
    return pm


def icon(name: str, size: int = 20, colour: str | None = None) -> QIcon:
    """Return a QIcon with normal, active and disabled renderings."""
    result = QIcon()
    base = colour or PALETTE.text_dim
    for ratio in (1.0, 1.5, 2.0):
        result.addPixmap(pixmap(name, size, base, ratio), QIcon.Normal, QIcon.Off)
        result.addPixmap(pixmap(name, size, PALETTE.text, ratio), QIcon.Active, QIcon.Off)
        result.addPixmap(pixmap(name, size, PALETTE.accent, ratio), QIcon.Normal, QIcon.On)
        result.addPixmap(pixmap(name, size, PALETTE.text_disabled, ratio), QIcon.Disabled, QIcon.Off)
    return result


def colour_swatch(colour: str, size: int = 14, dashed: bool = False) -> QPixmap:
    """A small swatch used in legends and list rows.

    A dashed swatch marks the left side, so side is legible without relying on
    the colour or on reading the label text.
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(colour), 2.0)
    if dashed:
        pen.setStyle(Qt.DashLine)
    painter.setPen(pen)
    painter.setBrush(QColor(colour).darker(160))
    painter.drawRoundedRect(QRectF(1.5, 1.5, size - 3, size - 3), 2, 2)
    painter.end()
    return pm


def application_icon(size: int = 64) -> QIcon:
    """The application icon: a stylised mandibular curve with measurement marks."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    scale = size / 64.0
    p.scale(scale, scale)

    p.setBrush(QColor(PALETTE.header))
    p.setPen(QPen(QColor(PALETTE.border), 2))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 10, 10)

    curve = QPainterPath(QPointF(12, 22))
    curve.cubicTo(QPointF(14, 46), QPointF(50, 46), QPointF(52, 22))
    p.setPen(QPen(QColor(PALETTE.accent), 3.4, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawPath(curve)

    inner = QPainterPath(QPointF(18, 22))
    inner.cubicTo(QPointF(20, 39), QPointF(44, 39), QPointF(46, 22))
    p.setPen(QPen(QColor(PALETTE.text_dim), 2.0, Qt.DashLine))
    p.drawPath(inner)

    p.setPen(QPen(QColor("#E69F00"), 2.6, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(32, 30), QPointF(32, 43))
    p.setBrush(QColor("#E69F00"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(32, 26), 3.2, 3.2)
    p.end()
    return QIcon(pm)


def clear_cache() -> None:
    """Drop cached pixmaps, for example after a theme change."""
    _CACHE.clear()
