"""Graphics items for annotation geometry.

The scene coordinate system is the image pixel coordinate system: one scene unit
is one image pixel, and the image is placed at the origin. Zooming changes the
view transform and never the scene, so an item's stored position is already the
value that gets written to the database. That is the structural reason
acceptance criterion AC 003 holds rather than something the code has to remember
to do.

Pens are cosmetic, meaning their width is in screen pixels. A contour stays
readable at low zoom and does not grow into a thick band at high zoom, which
matters when an annotator is judging a cortical margin a few pixels wide.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
)

from ...core.schema import GeometryType, LineStyle, Side, get_class
from ..theme import PALETTE

#: Z ordering so landmarks stay clickable above filled regions.
Z_REGION = 10
Z_CONTOUR = 20
Z_LINE = 30
Z_POINT = 40
Z_HANDLE = 90
Z_LABEL = 95

QT_LINE_STYLES = {
    LineStyle.SOLID: Qt.SolidLine,
    LineStyle.DASHED: Qt.DashLine,
    LineStyle.DOTTED: Qt.DotLine,
    LineStyle.DASH_DOT: Qt.DashDotLine,
}


class HandleItem(QGraphicsItem):
    """A draggable vertex handle.

    Handles are drawn at a fixed screen size by ignoring view transformations,
    so they stay grabbable when zoomed out and do not cover the anatomy when
    zoomed in.
    """

    def __init__(self, parent: "AnnotationGraphicsItem", index: int, size: float = 8.0):
        super().__init__(parent)
        self.index = index
        self.size = size
        self._hover = False
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setAcceptHoverEvents(True)
        self.setZValue(Z_HANDLE)
        self.setCursor(Qt.SizeAllCursor)

    def boundingRect(self) -> QRectF:
        half = self.size / 2.0 + 2.0
        return QRectF(-half, -half, half * 2, half * 2)

    def paint(self, painter, option, widget=None) -> None:
        parent = self.parentItem()
        colour = QColor(parent.colour if parent else PALETTE.accent)
        half = self.size / 2.0
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(PALETTE.canvas), 1.0))
        painter.setBrush(QBrush(colour.lighter(140) if self._hover else colour))
        painter.drawRect(QRectF(-half, -half, self.size, self.size))

    def hoverEnterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)


class AnnotationGraphicsItem(QGraphicsObject):
    """Base for every drawn annotation.

    The item owns a copy of the point list in scene coordinates. The controller
    writes those points back to the store when an edit finishes, which keeps
    autosave batched per gesture rather than per mouse move.
    """

    def __init__(self, annotation, controller=None):
        super().__init__()
        self.annotation = annotation
        self.controller = controller
        self._points: list = [QPointF(x, y) for x, y in annotation.points()]
        self._handles: list = []
        self._show_handles = False
        self._hover = False
        self._label_visible = True
        self._line_width = 2.0
        self._handle_size = 8.0

        try:
            self.label_class = get_class(annotation.class_key)
            self.colour = self.label_class.colour
            self.style = self.label_class.line_style_for(Side(annotation.side))
            self.short_code = self.label_class.label_for(Side(annotation.side))
        except (KeyError, ValueError):
            self.label_class = None
            self.colour = PALETTE.text_dim
            self.style = LineStyle.SOLID
            self.short_code = annotation.class_key

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(Z_LINE)
        self._apply_state()

    # -- state ---------------------------------------------------------------

    def _apply_state(self) -> None:
        self.setVisible(not self.annotation.hidden)
        self.setOpacity(0.45 if self.annotation.presence != "present" else 1.0)

    def set_line_width(self, width: float) -> None:
        self._line_width = width
        self.update()

    def set_handle_size(self, size: float) -> None:
        self._handle_size = size
        for h in self._handles:
            h.size = size
            h.update()

    def set_label_visible(self, visible: bool) -> None:
        self._label_visible = visible
        self.update()

    def set_colour(self, colour: str) -> None:
        self.colour = colour
        self.update()

    def points(self) -> list:
        return [(p.x(), p.y()) for p in self._points]

    def set_points(self, points) -> None:
        self.prepareGeometryChange()
        self._points = [QPointF(x, y) for x, y in points]
        self._sync_handles()
        self.update()

    def refresh_from_annotation(self) -> None:
        self.set_points(self.annotation.points())
        try:
            self.style = self.label_class.line_style_for(Side(self.annotation.side))
            self.short_code = self.label_class.label_for(Side(self.annotation.side))
        except (AttributeError, ValueError):
            pass
        self._apply_state()
        self.update()

    # -- handles -------------------------------------------------------------

    def set_handles_visible(self, visible: bool) -> None:
        if visible == self._show_handles:
            return
        self._show_handles = visible
        self._sync_handles()

    def _sync_handles(self) -> None:
        if not self._show_handles or self.annotation.locked:
            for h in self._handles:
                h.setParentItem(None)
                if h.scene():
                    h.scene().removeItem(h)
            self._handles = []
            return

        while len(self._handles) < len(self._points):
            handle = HandleItem(self, len(self._handles), self._handle_size)
            self._handles.append(handle)
        while len(self._handles) > len(self._points):
            handle = self._handles.pop()
            handle.setParentItem(None)
            if handle.scene():
                handle.scene().removeItem(handle)

        for i, handle in enumerate(self._handles):
            handle.index = i
            handle.setPos(self._points[i])
            handle.setVisible(True)

    def handle_at(self, scene_pos: QPointF, tolerance: float) -> int:
        """Index of the handle near a scene position, or minus one."""
        best, best_distance = -1, tolerance
        for i, p in enumerate(self._points):
            d = (p - scene_pos).manhattanLength()
            if d <= best_distance:
                best, best_distance = i, d
        return best

    def move_handle(self, index: int, scene_pos: QPointF) -> None:
        if 0 <= index < len(self._points) and not self.annotation.locked:
            self.prepareGeometryChange()
            self._points[index] = scene_pos
            if index < len(self._handles):
                self._handles[index].setPos(scene_pos)
            self.update()

    def insert_point(self, index: int, scene_pos: QPointF) -> None:
        self.prepareGeometryChange()
        self._points.insert(index, scene_pos)
        self._sync_handles()
        self.update()

    def remove_point(self, index: int) -> bool:
        minimum = self.label_class.min_vertices if self.label_class else 1
        if len(self._points) <= minimum:
            return False
        self.prepareGeometryChange()
        del self._points[index]
        self._sync_handles()
        self.update()
        return True

    def translate_by(self, delta: QPointF) -> None:
        if self.annotation.locked:
            return
        self.prepareGeometryChange()
        self._points = [p + delta for p in self._points]
        self._sync_handles()
        self.update()

    # -- painting helpers ----------------------------------------------------

    def _pen(self, selected: bool) -> QPen:
        colour = QColor(self.colour)
        width = self._line_width + (1.5 if selected else 0.0)
        pen = QPen(colour, width)
        pen.setCosmetic(True)      # width in screen pixels, independent of zoom
        pen.setStyle(QT_LINE_STYLES.get(self.style, Qt.SolidLine))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        if self.annotation.locked:
            pen.setStyle(Qt.DotLine)
        return pen

    def _halo_pen(self) -> QPen:
        """A dark outline behind the stroke, so a bright annotation stays
        visible over a bright region of the radiograph."""
        pen = QPen(QColor(0, 0, 0, 170), self._line_width + 2.5)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def _draw_label(self, painter, anchor: QPointF) -> None:
        if not self._label_visible or not self.short_code:
            return
        painter.save()
        painter.resetTransform()
        view = painter.device()
        try:
            screen = self.scene().views()[0].mapFromScene(anchor)
        except (IndexError, AttributeError):
            painter.restore()
            return

        font = QFont()
        font.setPointSizeF(8.0)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = self.short_code
        width = metrics.horizontalAdvance(text) + 8
        height = metrics.height() + 2
        x = screen.x() + 9
        y = screen.y() - height - 4

        rect = QRectF(x, y, width, height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 185))
        painter.drawRoundedRect(rect, 2, 2)
        painter.setPen(QPen(QColor(self.colour)))
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.restore()

    def _selection_marker(self, painter) -> None:
        if not self.isSelected():
            return
        pen = QPen(QColor(PALETTE.accent), 1.0, Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.boundingRect())

    def hoverEnterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def describe(self) -> str:
        name = self.label_class.display_name if self.label_class else self.annotation.class_key
        side = Side(self.annotation.side).display
        return f"{name} ({side})"


class PointAnnotationItem(AnnotationGraphicsItem):
    """A landmark, drawn as a crosshair with a centre dot."""

    def __init__(self, annotation, controller=None):
        super().__init__(annotation, controller)
        self.setZValue(Z_POINT)
        self._radius = 7.0

    def set_handle_size(self, size: float) -> None:
        super().set_handle_size(size)
        self.prepareGeometryChange()
        self._radius = max(4.0, size)
        self.update()

    def boundingRect(self) -> QRectF:
        if not self._points:
            return QRectF()
        p = self._points[0]
        r = self._radius * 3
        return QRectF(p.x() - r, p.y() - r, r * 2, r * 2)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if self._points:
            p = self._points[0]
            r = self._radius
            path.addEllipse(p, r, r)
        return path

    def paint(self, painter, option, widget=None) -> None:
        if not self._points:
            return
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        p = self._points[0]

        view = self.scene().views()[0] if self.scene() and self.scene().views() else None
        scale = view.transform().m11() if view else 1.0
        r = self._radius / max(0.05, scale)
        gap = r * 0.45

        selected = self.isSelected()
        for pen in (self._halo_pen(), self._pen(selected)):
            painter.setPen(pen)
            painter.drawLine(QPointF(p.x() - r, p.y()), QPointF(p.x() - gap, p.y()))
            painter.drawLine(QPointF(p.x() + gap, p.y()), QPointF(p.x() + r, p.y()))
            painter.drawLine(QPointF(p.x(), p.y() - r), QPointF(p.x(), p.y() - gap))
            painter.drawLine(QPointF(p.x(), p.y() + gap), QPointF(p.x(), p.y() + r))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.colour))
        painter.drawEllipse(p, gap * 0.55, gap * 0.55)

        if self.annotation.ambiguous:
            pen = QPen(QColor(PALETTE.warning), 1.2, Qt.DotLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(p, r * 1.45, r * 1.45)

        self._draw_label(painter, p)


class LineAnnotationItem(AnnotationGraphicsItem):
    """A two endpoint measurement line, with end ticks."""

    def boundingRect(self) -> QRectF:
        if len(self._points) < 2:
            return QRectF()
        rect = QRectF(self._points[0], self._points[-1]).normalized()
        return rect.adjusted(-14, -14, 14, 14)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if len(self._points) >= 2:
            path.moveTo(self._points[0])
            path.lineTo(self._points[-1])
            stroker = QPainterPathStroker()
            stroker.setWidth(8.0)
            return stroker.createStroke(path)
        return path

    def paint(self, painter, option, widget=None) -> None:
        if len(self._points) < 2:
            return
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        a, b = self._points[0], self._points[-1]

        view = self.scene().views()[0] if self.scene() and self.scene().views() else None
        scale = view.transform().m11() if view else 1.0
        tick = 5.0 / max(0.05, scale)

        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = (dx * dx + dy * dy) ** 0.5
        if length > 1e-6:
            nx, ny = -dy / length * tick, dx / length * tick
        else:
            nx, ny = tick, 0.0

        for pen in (self._halo_pen(), self._pen(self.isSelected())):
            painter.setPen(pen)
            painter.drawLine(a, b)
            painter.drawLine(QPointF(a.x() - nx, a.y() - ny), QPointF(a.x() + nx, a.y() + ny))
            painter.drawLine(QPointF(b.x() - nx, b.y() - ny), QPointF(b.x() + nx, b.y() + ny))

        self._draw_label(painter, QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))


class PolylineAnnotationItem(AnnotationGraphicsItem):
    """An open contour."""

    def __init__(self, annotation, controller=None):
        super().__init__(annotation, controller)
        self.setZValue(Z_CONTOUR)

    def boundingRect(self) -> QRectF:
        if not self._points:
            return QRectF()
        path = QPainterPath()
        path.addPolygon(QPolygonF(self._points))
        return path.boundingRect().adjusted(-14, -14, 14, 14)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if len(self._points) >= 2:
            path.moveTo(self._points[0])
            for p in self._points[1:]:
                path.lineTo(p)
            stroker = QPainterPathStroker()
            stroker.setWidth(8.0)
            return stroker.createStroke(path)
        return path

    def paint(self, painter, option, widget=None) -> None:
        if len(self._points) < 2:
            if self._points:
                painter.setPen(self._pen(self.isSelected()))
                painter.drawPoint(self._points[0])
            return
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        polygon = QPolygonF(self._points)
        for pen in (self._halo_pen(), self._pen(self.isSelected())):
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(polygon)
        self._draw_label(painter, self._points[len(self._points) // 2])


class PolygonAnnotationItem(AnnotationGraphicsItem):
    """A closed region with a translucent fill."""

    def __init__(self, annotation, controller=None):
        super().__init__(annotation, controller)
        self.setZValue(Z_REGION)
        self.fill_alpha = 48

    def boundingRect(self) -> QRectF:
        if not self._points:
            return QRectF()
        return QPolygonF(self._points).boundingRect().adjusted(-14, -14, 14, 14)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if len(self._points) >= 3:
            path.addPolygon(QPolygonF(self._points))
            path.closeSubpath()
        return path

    def paint(self, painter, option, widget=None) -> None:
        if len(self._points) < 2:
            return
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        polygon = QPolygonF(self._points)
        fill = QColor(self.colour)
        fill.setAlpha(self.fill_alpha + (26 if self._hover else 0))
        painter.setPen(self._halo_pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(polygon)
        painter.setPen(self._pen(self.isSelected()))
        painter.setBrush(QBrush(fill))
        painter.drawPolygon(polygon)
        if self._points:
            self._draw_label(painter, polygon.boundingRect().topLeft())


class BoxAnnotationItem(AnnotationGraphicsItem):
    """An axis aligned box defined by two opposite corners."""

    def __init__(self, annotation, controller=None):
        super().__init__(annotation, controller)
        self.setZValue(Z_REGION)
        self.fill_alpha = 30

    def rect(self) -> QRectF:
        if len(self._points) < 2:
            return QRectF()
        return QRectF(self._points[0], self._points[-1]).normalized()

    def boundingRect(self) -> QRectF:
        return self.rect().adjusted(-14, -14, 14, 14)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.rect())
        return path

    def paint(self, painter, option, widget=None) -> None:
        rect = self.rect()
        if rect.isNull():
            return
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        fill = QColor(self.colour)
        fill.setAlpha(self.fill_alpha + (24 if self._hover else 0))
        painter.setPen(self._halo_pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)
        painter.setPen(self._pen(self.isSelected()))
        painter.setBrush(QBrush(fill))
        painter.drawRect(rect)
        self._draw_label(painter, rect.topLeft())


class RoiAnnotationItem(BoxAnnotationItem):
    """A fixed size analysis region.

    The size is part of the protocol rather than a free choice, because region
    size changes the texture values. The drawn size is shown on the item so the
    annotator can see what was used.
    """

    def __init__(self, annotation, controller=None, fixed_size: int = 64):
        super().__init__(annotation, controller)
        self.fixed_size = fixed_size
        self.fill_alpha = 22

    def paint(self, painter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        rect = self.rect()
        if rect.isNull():
            return
        pen = QPen(QColor(self.colour), 0.8, Qt.DotLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for i in (1, 2, 3):
            x = rect.left() + rect.width() * i / 4.0
            y = rect.top() + rect.height() * i / 4.0
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))


class MaskAnnotationItem(AnnotationGraphicsItem):
    """A raster brush mask, drawn from a run length encoded payload."""

    def __init__(self, annotation, controller=None, mask=None):
        super().__init__(annotation, controller)
        self.setZValue(Z_REGION)
        self._mask = mask
        self._pixmap = None
        self._origin = QPointF(0, 0)
        if mask is not None:
            self.set_mask(mask, annotation.mask_bbox)

    def set_mask(self, mask, bbox) -> None:
        from PySide6.QtGui import QImage, QPixmap
        import numpy as np

        self.prepareGeometryChange()
        self._mask = mask
        if mask is None or not mask.any():
            self._pixmap = None
            return

        colour = QColor(self.colour)
        h, w = mask.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = colour.red()
        rgba[..., 1] = colour.green()
        rgba[..., 2] = colour.blue()
        rgba[..., 3] = np.where(mask, 110, 0).astype(np.uint8)
        image = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
        self._pixmap = QPixmap.fromImage(image)
        self._origin = QPointF(bbox[0] if bbox else 0, bbox[1] if bbox else 0)
        self.update()

    def boundingRect(self) -> QRectF:
        if self._pixmap is None:
            return QRectF()
        return QRectF(
            self._origin.x(), self._origin.y(),
            self._pixmap.width(), self._pixmap.height(),
        )

    def paint(self, painter, option, widget=None) -> None:
        if self._pixmap is None:
            return
        painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform, False)
        painter.drawPixmap(self.boundingRect(), self._pixmap, QRectF(self._pixmap.rect()))
        if self.isSelected():
            pen = QPen(QColor(PALETTE.accent), 1.0, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect())


ITEM_FOR_GEOMETRY = {
    GeometryType.POINT: PointAnnotationItem,
    GeometryType.LINE: LineAnnotationItem,
    GeometryType.POLYLINE: PolylineAnnotationItem,
    GeometryType.POLYGON: PolygonAnnotationItem,
    GeometryType.BOX: BoxAnnotationItem,
    GeometryType.ROI_RECT: RoiAnnotationItem,
    GeometryType.MASK: MaskAnnotationItem,
}


def create_item(annotation, controller=None, roi_size: int = 64) -> AnnotationGraphicsItem:
    """Build the right item for an annotation's geometry type."""
    try:
        geometry = GeometryType(annotation.geometry_type)
    except ValueError:
        geometry = GeometryType.POINT
    cls = ITEM_FOR_GEOMETRY.get(geometry, PointAnnotationItem)
    if cls is RoiAnnotationItem:
        return cls(annotation, controller, roi_size)
    return cls(annotation, controller)
