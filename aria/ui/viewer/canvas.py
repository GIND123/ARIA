"""The image canvas and its interaction model.

The scene is the image: one scene unit is one image pixel. Zoom and pan act on
the view transform only, so nothing an annotator does to the display can move a
stored coordinate (FR 004, NFR 008, AC 003).

Mouse model, chosen to match what people already expect from imaging software:

============  ===============================================================
Input         Action
============  ===============================================================
Left          the active tool
Middle drag   pan
Right drag    window and level, horizontal is width, vertical is centre
Wheel         zoom about the cursor
Shift+wheel   scroll vertically
Space         temporary pan, while held
============  ===============================================================
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QRubberBand,
)

from ...core.models import Annotation, new_id
from ...core.schema import GeometryType, Side, get_class
from ...io.image import DisplaySettings, ImageData
from ..theme import PALETTE
from .items import AnnotationGraphicsItem, create_item


class Tool:
    """Identifiers for the interaction tools."""

    SELECT = "select"
    POINT = "point"
    LINE = "line"
    POLYLINE = "polyline"
    POLYGON = "polygon"
    BOX = "box"
    ROI = "roi"
    BRUSH = "brush"
    ERASER = "eraser"
    RULER = "ruler"          # calibration measurement, not stored as a label
    PAN = "pan"
    ZOOM = "zoom"
    WINDOW_LEVEL = "window_level"

    DRAWING = {POINT, LINE, POLYLINE, POLYGON, BOX, ROI, BRUSH, ERASER}


#: Distance in screen pixels within which a click grabs a handle or snaps.
GRAB_TOLERANCE = 10.0
SNAP_TOLERANCE = 14.0


class ImageCanvas(QGraphicsView):
    """Displays one image and its annotations, and handles editing gestures."""

    cursor_moved = Signal(float, float)             # scene x, y
    cursor_left = Signal()
    zoom_changed = Signal(float)
    display_settings_changed = Signal(object)
    annotation_created = Signal(object)             # Annotation
    annotation_edited = Signal(str, list)           # id, points
    annotation_edit_finished = Signal(str)          # id
    selection_changed = Signal(list)                # list of annotation ids
    ruler_measured = Signal(float, tuple, tuple)    # length px, start, end
    status_message = Signal(str)
    context_requested = Signal(object, object)      # annotation id, global pos

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.image: ImageData | None = None
        self.settings = DisplaySettings()
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._items: dict = {}

        self.active_tool = Tool.SELECT
        self.active_class = "mental_foramen_centre"
        self.active_side = Side.RIGHT
        self.roi_size = 64
        self.snap_enabled = True
        self.crosshair_enabled = True
        self.magnifier_enabled = False
        self.magnifier_factor = 4.0
        self.annotation_line_width = 2.0
        self.landmark_size = 7.0
        self.labels_visible = True
        self.read_only = False
        self.zoom_step = 1.15

        # Interaction state.
        self._dragging_handle: tuple | None = None
        self._dragging_item: AnnotationGraphicsItem | None = None
        self._drag_origin = QPointF()
        self._panning = False
        self._pan_anchor = QPoint()
        self._windowing = False
        self._window_anchor = QPoint()
        self._window_start = (0.0, 1.0)
        self._space_pan = False
        self._pending_points: list = []
        self._pending_item = None
        self._ruler_points: list = []
        self._brush_radius = 12.0
        self._brush_mask = None
        self._brush_target = None
        self._cursor_scene = QPointF()
        self._cursor_in_view = False
        self._rubber_band: QRubberBand | None = None
        self._rubber_origin = QPoint()

        self._configure_view()

    # -- setup ---------------------------------------------------------------

    def _configure_view(self) -> None:
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setBackgroundBrush(QColor(PALETTE.canvas))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFrameShape(QGraphicsView.NoFrame)
        self._scene.selectionChanged.connect(self._emit_selection)

    # -- image ---------------------------------------------------------------

    def set_image(self, image: ImageData | None, settings: DisplaySettings | None = None) -> None:
        """Load an image, replacing whatever was displayed."""
        self.clear_annotations()
        self.image = image
        if image is None:
            if self._pixmap_item is not None:
                self._scene.removeItem(self._pixmap_item)
                self._pixmap_item = None
            self._scene.setSceneRect(QRectF(0, 0, 1, 1))
            self.viewport().update()
            return

        self.settings = settings or image.default_display_settings()
        if self._pixmap_item is None:
            self._pixmap_item = QGraphicsPixmapItem()
            self._pixmap_item.setZValue(-100)
            self._pixmap_item.setTransformationMode(Qt.SmoothTransformation)
            self._scene.addItem(self._pixmap_item)
        self._scene.setSceneRect(QRectF(0, 0, image.columns, image.rows))
        self.refresh_display()
        self.fit_to_window()

    def refresh_display(self) -> None:
        """Rebuild the displayed pixmap from the current display settings.

        This touches the display only. ``image.pixels`` is never written to.
        """
        if self.image is None or self._pixmap_item is None:
            return
        array = self.image.to_display(self.settings)
        array = np.ascontiguousarray(array)
        h, w = array.shape
        qimage = QImage(array.data, w, h, w, QImage.Format_Grayscale8).copy()
        self._pixmap_item.setPixmap(QPixmap.fromImage(qimage))
        self._pixmap_item.setPos(0, 0)
        self.viewport().update()

    def apply_display_settings(self, settings: DisplaySettings) -> None:
        self.settings = settings
        self.refresh_display()
        self.display_settings_changed.emit(self.settings)

    # -- annotations ---------------------------------------------------------

    def clear_annotations(self) -> None:
        for item in list(self._items.values()):
            if item.scene():
                self._scene.removeItem(item)
        self._items = {}
        self._pending_points = []
        if self._pending_item is not None and self._pending_item.scene():
            self._scene.removeItem(self._pending_item)
        self._pending_item = None

    def load_annotations(self, annotations) -> None:
        self.clear_annotations()
        for annotation in annotations:
            self.add_annotation_item(annotation)

    def add_annotation_item(self, annotation) -> AnnotationGraphicsItem:
        item = create_item(annotation, self, self.roi_size)
        item.set_line_width(self.annotation_line_width)
        item.set_handle_size(self.landmark_size)
        item.set_label_visible(self.labels_visible)
        if annotation.geometry_type == GeometryType.MASK.value and annotation.mask_rle:
            mask, bbox = decode_mask(annotation.mask_rle, annotation.mask_bbox)
            if mask is not None:
                item.set_mask(mask, bbox)
        self._scene.addItem(item)
        self._items[annotation.id] = item
        return item

    def remove_annotation_item(self, annotation_id: str) -> None:
        item = self._items.pop(annotation_id, None)
        if item is not None and item.scene():
            self._scene.removeItem(item)

    def update_annotation_item(self, annotation) -> None:
        item = self._items.get(annotation.id)
        if item is None:
            self.add_annotation_item(annotation)
            return
        item.annotation = annotation
        item.refresh_from_annotation()

    def item_for(self, annotation_id: str) -> AnnotationGraphicsItem | None:
        return self._items.get(annotation_id)

    def select_annotation(self, annotation_id: str, exclusive: bool = True) -> None:
        if exclusive:
            self._scene.clearSelection()
        item = self._items.get(annotation_id)
        if item is not None:
            item.setSelected(True)
            item.set_handles_visible(True)

    def selected_ids(self) -> list:
        return [
            aid for aid, item in self._items.items()
            if item.isSelected() and item.isVisible()
        ]

    def _emit_selection(self) -> None:
        selected = self.selected_ids()
        for aid, item in self._items.items():
            item.set_handles_visible(aid in selected and not self.read_only)
        self.selection_changed.emit(selected)

    def set_labels_visible(self, visible: bool) -> None:
        self.labels_visible = visible
        for item in self._items.values():
            item.set_label_visible(visible)

    def set_annotation_line_width(self, width: float) -> None:
        self.annotation_line_width = width
        for item in self._items.values():
            item.set_line_width(width)

    def set_landmark_size(self, size: float) -> None:
        self.landmark_size = size
        for item in self._items.values():
            item.set_handle_size(size)

    # -- view control --------------------------------------------------------

    def zoom_factor(self) -> float:
        return float(self.transform().m11())

    def set_zoom(self, factor: float, anchor_scene: QPointF | None = None) -> None:
        factor = max(0.02, min(80.0, factor))
        current = self.zoom_factor()
        if abs(current) < 1e-9:
            return
        if anchor_scene is None:
            anchor_scene = self.mapToScene(self.viewport().rect().center())
        anchor_view = self.mapFromScene(anchor_scene)
        self.setTransform(QTransform().scale(factor, factor))
        new_view = self.mapFromScene(anchor_scene)
        delta = new_view - anchor_view
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
        self.zoom_changed.emit(factor)
        self.viewport().update()

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom_factor() * self.zoom_step, self._cursor_scene if self._cursor_in_view else None)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom_factor() / self.zoom_step, self._cursor_scene if self._cursor_in_view else None)

    def fit_to_window(self) -> None:
        if self.image is None:
            return
        rect = QRectF(0, 0, self.image.columns, self.image.rows)
        if rect.isEmpty():
            return
        viewport = self.viewport().rect()
        if viewport.width() < 4 or viewport.height() < 4:
            return
        factor = min(
            viewport.width() / rect.width(), viewport.height() / rect.height()
        ) * 0.98
        self.setTransform(QTransform().scale(factor, factor))
        self.centerOn(rect.center())
        self.zoom_changed.emit(factor)

    def zoom_to_actual(self) -> None:
        self.set_zoom(1.0)

    def reset_view(self) -> None:
        self.fit_to_window()
        if self.image is not None:
            self.apply_display_settings(self.image.default_display_settings())

    def centre_on_scene(self, x: float, y: float) -> None:
        self.centerOn(QPointF(x, y))

    def zoom_to_rect(self, rect: QRectF, margin: float = 1.15) -> None:
        if rect.isEmpty():
            return
        viewport = self.viewport().rect()
        factor = min(
            viewport.width() / (rect.width() * margin),
            viewport.height() / (rect.height() * margin),
        )
        self.set_zoom(factor)
        self.centerOn(rect.center())

    # -- tool state ----------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        self.cancel_pending()
        self.active_tool = tool
        cursors = {
            Tool.SELECT: Qt.ArrowCursor,
            Tool.PAN: Qt.OpenHandCursor,
            Tool.ZOOM: Qt.CrossCursor,
            Tool.WINDOW_LEVEL: Qt.SizeAllCursor,
            Tool.BRUSH: Qt.BlankCursor,
            Tool.ERASER: Qt.BlankCursor,
        }
        self.setCursor(cursors.get(tool, Qt.CrossCursor))
        self.viewport().update()

    def set_active_class(self, class_key: str, side: Side) -> None:
        self.cancel_pending()
        self.active_class = class_key
        self.active_side = side

    def cancel_pending(self) -> None:
        """Abandon a multi click shape that is part way through."""
        self._pending_points = []
        if self._pending_item is not None and self._pending_item.scene():
            self._scene.removeItem(self._pending_item)
        self._pending_item = None
        self._ruler_points = []
        self._brush_mask = None
        self._brush_target = None
        self.viewport().update()

    def finish_pending(self) -> None:
        """Commit a polyline or polygon that is being drawn."""
        geometry = self._geometry_for_active_class()
        minimum = 2 if geometry is GeometryType.POLYLINE else 3
        if len(self._pending_points) >= minimum:
            self._commit_new(self._pending_points)
        else:
            self.status_message.emit(
                f"A {geometry.value} needs at least {minimum} points. "
                f"The shape was not created."
            )
        self.cancel_pending()

    # -- mouse ---------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())

        if event.button() == Qt.MiddleButton or (
            event.button() == Qt.LeftButton and (self._space_pan or self.active_tool == Tool.PAN)
        ):
            self._panning = True
            self._pan_anchor = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.RightButton:
            if event.modifiers() & Qt.ShiftModifier:
                item = self._annotation_at(scene_pos)
                self.context_requested.emit(
                    item.annotation.id if item else None, event.globalPosition().toPoint()
                )
                event.accept()
                return
            self._windowing = True
            self._window_anchor = event.position().toPoint()
            self._window_start = (self.settings.window_centre, self.settings.window_width)
            self.setCursor(Qt.SizeAllCursor)
            event.accept()
            return

        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        if self.active_tool == Tool.ZOOM:
            self._rubber_origin = event.position().toPoint()
            if self._rubber_band is None:
                self._rubber_band = QRubberBand(QRubberBand.Rectangle, self.viewport())
            self._rubber_band.setGeometry(QRect(self._rubber_origin, QPoint()))
            self._rubber_band.show()
            event.accept()
            return

        if self.active_tool == Tool.RULER:
            self._ruler_points.append(scene_pos)
            if len(self._ruler_points) == 2:
                a, b = self._ruler_points
                length = ((b.x() - a.x()) ** 2 + (b.y() - a.y()) ** 2) ** 0.5
                self.ruler_measured.emit(length, (a.x(), a.y()), (b.x(), b.y()))
                self._ruler_points = []
            self.viewport().update()
            event.accept()
            return

        if self.read_only:
            self._select_at(scene_pos, event)
            event.accept()
            return

        if self.active_tool == Tool.SELECT:
            self._begin_select_or_edit(scene_pos, event)
            event.accept()
            return

        if self.active_tool in (Tool.BRUSH, Tool.ERASER):
            self._begin_brush(scene_pos)
            event.accept()
            return

        self._handle_draw_click(scene_pos, event)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        position = event.position().toPoint()
        scene_pos = self.mapToScene(position)
        self._cursor_scene = scene_pos
        self._cursor_in_view = True
        self.cursor_moved.emit(scene_pos.x(), scene_pos.y())

        if self._panning:
            delta = position - self._pan_anchor
            self._pan_anchor = position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return

        if self._windowing:
            self._apply_window_drag(position)
            event.accept()
            return

        if self._rubber_band is not None and self._rubber_band.isVisible():
            self._rubber_band.setGeometry(QRect(self._rubber_origin, position).normalized())
            event.accept()
            return

        if self._dragging_handle is not None:
            item, index = self._dragging_handle
            target = self._snapped(scene_pos, exclude=item)
            item.move_handle(index, target)
            self.annotation_edited.emit(item.annotation.id, item.points())
            event.accept()
            return

        if self._dragging_item is not None:
            delta = scene_pos - self._drag_origin
            self._drag_origin = scene_pos
            self._dragging_item.translate_by(delta)
            self.annotation_edited.emit(
                self._dragging_item.annotation.id, self._dragging_item.points()
            )
            event.accept()
            return

        if self._brush_target is not None and event.buttons() & Qt.LeftButton:
            self._paint_brush(scene_pos)
            event.accept()
            return

        if self._pending_points or self.active_tool in (Tool.BRUSH, Tool.ERASER) or self.crosshair_enabled:
            self.viewport().update()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning and event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._panning = False
            self.setCursor(Qt.OpenHandCursor if self.active_tool == Tool.PAN else Qt.ArrowCursor)
            self.set_tool(self.active_tool)
            event.accept()
            return

        if self._windowing and event.button() == Qt.RightButton:
            self._windowing = False
            self.set_tool(self.active_tool)
            self.display_settings_changed.emit(self.settings)
            event.accept()
            return

        if self._rubber_band is not None and self._rubber_band.isVisible():
            rect = self._rubber_band.geometry()
            self._rubber_band.hide()
            if rect.width() > 8 and rect.height() > 8:
                self.zoom_to_rect(self.mapToScene(rect).boundingRect())
            else:
                self.zoom_in()
            event.accept()
            return

        if self._dragging_handle is not None:
            item, _index = self._dragging_handle
            self._dragging_handle = None
            self.annotation_edit_finished.emit(item.annotation.id)
            event.accept()
            return

        if self._dragging_item is not None:
            item = self._dragging_item
            self._dragging_item = None
            self.annotation_edit_finished.emit(item.annotation.id)
            event.accept()
            return

        if self._brush_target is not None:
            self._commit_brush()
            event.accept()
            return

        if self.active_tool == Tool.BOX and len(self._pending_points) == 1:
            scene_pos = self.mapToScene(event.position().toPoint())
            if (scene_pos - self._pending_points[0]).manhattanLength() > 4:
                self._commit_new([self._pending_points[0], scene_pos])
                self.cancel_pending()
                event.accept()
                return

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._pending_points and self.active_tool in (Tool.POLYLINE, Tool.POLYGON):
            self.finish_pending()
            event.accept()
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        item = self._annotation_at(scene_pos)
        if item is not None and self.active_tool == Tool.SELECT and not self.read_only:
            # Double click on a contour inserts a vertex at that position.
            if isinstance(item.annotation.geometry_type, str) and item.annotation.geometry_type in (
                GeometryType.POLYLINE.value, GeometryType.POLYGON.value
            ):
                index = _nearest_segment(item.points(), (scene_pos.x(), scene_pos.y()))
                item.insert_point(index + 1, scene_pos)
                self.annotation_edited.emit(item.annotation.id, item.points())
                self.annotation_edit_finished.emit(item.annotation.id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ShiftModifier:
            super().wheelEvent(event)
            return
        if event.modifiers() & Qt.ControlModifier and self.active_tool in (Tool.BRUSH, Tool.ERASER):
            step = 1.0 if event.angleDelta().y() > 0 else -1.0
            self._brush_radius = max(2.0, min(200.0, self._brush_radius + step * 2))
            self.viewport().update()
            event.accept()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        anchor = self.mapToScene(event.position().toPoint())
        factor = self.zoom_step if delta > 0 else 1.0 / self.zoom_step
        self.set_zoom(self.zoom_factor() * factor, anchor)
        event.accept()

    def leaveEvent(self, event) -> None:
        self._cursor_in_view = False
        self.cursor_left.emit()
        self.viewport().update()
        super().leaveEvent(event)

    # -- keyboard ------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pan = True
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        if key == Qt.Key_Escape:
            if self._pending_points or self._ruler_points:
                self.cancel_pending()
                self.status_message.emit("Drawing cancelled.")
            else:
                self._scene.clearSelection()
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._pending_points:
                self.finish_pending()
                event.accept()
                return
        if key == Qt.Key_Backspace and self._pending_points:
            self._pending_points.pop()
            self._update_pending_preview()
            event.accept()
            return
        if key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_in()
            event.accept()
            return
        if key == Qt.Key_Minus:
            self.zoom_out()
            event.accept()
            return
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            if self._nudge_selection(key, event.modifiers()):
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pan = False
            self.set_tool(self.active_tool)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _nudge_selection(self, key, modifiers) -> bool:
        """Arrow keys move the selection by exactly one pixel.

        Sub pixel placement by mouse is limited by the display scale, so a
        keyboard nudge is the only way to place a landmark on an exact pixel at
        low zoom. The step is in image pixels, not screen pixels.
        """
        selected = [self._items[i] for i in self.selected_ids()]
        if not selected or self.read_only:
            return False
        step = 10.0 if modifiers & Qt.ShiftModifier else 1.0
        delta = {
            Qt.Key_Left: QPointF(-step, 0), Qt.Key_Right: QPointF(step, 0),
            Qt.Key_Up: QPointF(0, -step), Qt.Key_Down: QPointF(0, step),
        }[key]
        for item in selected:
            item.translate_by(delta)
            self.annotation_edited.emit(item.annotation.id, item.points())
            self.annotation_edit_finished.emit(item.annotation.id)
        return True

    # -- interaction helpers -------------------------------------------------

    def _annotation_at(self, scene_pos: QPointF) -> AnnotationGraphicsItem | None:
        tolerance = GRAB_TOLERANCE / max(0.05, self.zoom_factor())
        best, best_distance = None, float("inf")
        for item in self._items.values():
            if not item.isVisible() or item.annotation.hidden:
                continue
            shape = item.shape()
            if shape.contains(scene_pos):
                rect = item.boundingRect()
                distance = (rect.center() - scene_pos).manhattanLength()
                if distance < best_distance:
                    best, best_distance = item, distance
        if best is not None:
            return best
        for item in self._items.values():
            if not item.isVisible():
                continue
            if item.handle_at(scene_pos, tolerance) >= 0:
                return item
        return None

    def _select_at(self, scene_pos: QPointF, event) -> None:
        item = self._annotation_at(scene_pos)
        additive = bool(event.modifiers() & Qt.ControlModifier)
        if not additive:
            self._scene.clearSelection()
        if item is not None:
            item.setSelected(True)

    def _begin_select_or_edit(self, scene_pos: QPointF, event) -> None:
        tolerance = GRAB_TOLERANCE / max(0.05, self.zoom_factor())

        for aid in self.selected_ids():
            item = self._items[aid]
            index = item.handle_at(scene_pos, tolerance)
            if index >= 0 and not item.annotation.locked:
                if event.modifiers() & Qt.AltModifier:
                    if item.remove_point(index):
                        self.annotation_edited.emit(item.annotation.id, item.points())
                        self.annotation_edit_finished.emit(item.annotation.id)
                    else:
                        self.status_message.emit(
                            "This shape already has the minimum number of points."
                        )
                    return
                self._dragging_handle = (item, index)
                return

        item = self._annotation_at(scene_pos)
        additive = bool(event.modifiers() & Qt.ControlModifier)
        if item is None:
            if not additive:
                self._scene.clearSelection()
            return

        if not additive:
            self._scene.clearSelection()
        item.setSelected(True)

        if event.modifiers() & Qt.ShiftModifier and not item.annotation.locked:
            self._dragging_item = item
            self._drag_origin = scene_pos

    def _handle_draw_click(self, scene_pos: QPointF, event) -> None:
        geometry = self._geometry_for_active_class()
        target = self._snapped(scene_pos)

        if geometry is GeometryType.POINT:
            self._commit_new([target])
            return
        if geometry in (GeometryType.ROI_RECT,):
            half = self.roi_size / 2.0
            self._commit_new(
                [
                    QPointF(target.x() - half, target.y() - half),
                    QPointF(target.x() + half, target.y() + half),
                ]
            )
            return
        if geometry is GeometryType.BOX:
            self._pending_points = [target]
            return
        if geometry is GeometryType.LINE:
            self._pending_points.append(target)
            if len(self._pending_points) == 2:
                self._commit_new(self._pending_points)
                self.cancel_pending()
            else:
                self._update_pending_preview()
            return
        # Polyline and polygon collect points until the annotator finishes.
        self._pending_points.append(target)
        self._update_pending_preview()

    def _geometry_for_active_class(self) -> GeometryType:
        tool_geometry = {
            Tool.POINT: GeometryType.POINT,
            Tool.LINE: GeometryType.LINE,
            Tool.POLYLINE: GeometryType.POLYLINE,
            Tool.POLYGON: GeometryType.POLYGON,
            Tool.BOX: GeometryType.BOX,
            Tool.ROI: GeometryType.ROI_RECT,
        }
        if self.active_tool in tool_geometry:
            return tool_geometry[self.active_tool]
        try:
            return get_class(self.active_class).geometry
        except KeyError:
            return GeometryType.POINT

    def _update_pending_preview(self) -> None:
        self.viewport().update()

    def _commit_new(self, points) -> None:
        geometry = self._geometry_for_active_class()
        try:
            cls = get_class(self.active_class)
            if cls.geometry is not geometry and self.active_tool not in Tool.DRAWING:
                geometry = cls.geometry
        except KeyError:
            pass

        annotation = Annotation(
            id=new_id("ann_"),
            class_key=self.active_class,
            side=self.active_side.value,
            geometry_type=geometry.value,
        )
        annotation.set_points([(p.x(), p.y()) for p in points])
        self.annotation_created.emit(annotation)

    def _snapped(self, scene_pos: QPointF, exclude=None) -> QPointF:
        """Snap to a nearby contour when snapping is on.

        Snapping matters here because the cortical width endpoints are supposed
        to sit on the traced borders. Without it an annotator places them by eye
        and the measurement inherits that error.
        """
        if not self.snap_enabled:
            return scene_pos
        tolerance = SNAP_TOLERANCE / max(0.05, self.zoom_factor())
        best, best_distance = None, tolerance
        for item in self._items.values():
            if item is exclude or not item.isVisible():
                continue
            if item.annotation.geometry_type not in (
                GeometryType.POLYLINE.value, GeometryType.POLYGON.value
            ):
                continue
            points = item.points()
            if len(points) < 2:
                continue
            from ...core.geometry import closest_point_on_polyline

            candidate, _, _ = closest_point_on_polyline(
                (scene_pos.x(), scene_pos.y()), points
            )
            distance = (
                (candidate[0] - scene_pos.x()) ** 2 + (candidate[1] - scene_pos.y()) ** 2
            ) ** 0.5
            if distance < best_distance:
                best, best_distance = candidate, distance
        return QPointF(best[0], best[1]) if best else scene_pos

    def _apply_window_drag(self, position: QPoint) -> None:
        if self.image is None:
            return
        delta = position - self._window_anchor
        low, high = self.image.value_range()
        span = max(1.0, high - low)
        centre = self._window_start[0] + delta.y() * span / 400.0
        width = max(1.0, self._window_start[1] + delta.x() * span / 400.0)
        self.settings.window_centre = centre
        self.settings.window_width = width
        self.refresh_display()
        self.status_message.emit(
            f"Window centre {centre:.0f}, width {width:.0f}"
        )

    # -- brush ---------------------------------------------------------------

    def _begin_brush(self, scene_pos: QPointF) -> None:
        if self.image is None:
            return
        selected = self.selected_ids()
        target = None
        for aid in selected:
            if self._items[aid].annotation.geometry_type == GeometryType.MASK.value:
                target = self._items[aid]
                break
        if target is None:
            annotation = Annotation(
                id=new_id("ann_"),
                class_key=self.active_class,
                side=self.active_side.value,
                geometry_type=GeometryType.MASK.value,
            )
            self._brush_mask = np.zeros((self.image.rows, self.image.columns), dtype=bool)
            self._brush_target = annotation
        else:
            self._brush_target = target.annotation
            mask, _ = decode_mask(target.annotation.mask_rle, target.annotation.mask_bbox)
            if mask is None:
                mask = np.zeros((self.image.rows, self.image.columns), dtype=bool)
            elif mask.shape != (self.image.rows, self.image.columns):
                full = np.zeros((self.image.rows, self.image.columns), dtype=bool)
                bbox = target.annotation.mask_bbox or [0, 0]
                full[
                    int(bbox[1]) : int(bbox[1]) + mask.shape[0],
                    int(bbox[0]) : int(bbox[0]) + mask.shape[1],
                ] = mask
                mask = full
            self._brush_mask = mask
        self._paint_brush(scene_pos)

    def _paint_brush(self, scene_pos: QPointF) -> None:
        if self._brush_mask is None:
            return
        rows, cols = self._brush_mask.shape
        cx, cy = scene_pos.x(), scene_pos.y()
        r = self._brush_radius
        x0, x1 = max(0, int(cx - r)), min(cols, int(cx + r) + 1)
        y0, y1 = max(0, int(cy - r)), min(rows, int(cy + r) + 1)
        if x1 <= x0 or y1 <= y0:
            return
        ys, xs = np.mgrid[y0:y1, x0:x1]
        disc = ((xs + 0.5 - cx) ** 2 + (ys + 0.5 - cy) ** 2) <= r * r
        if self.active_tool == Tool.ERASER:
            self._brush_mask[y0:y1, x0:x1] &= ~disc
        else:
            self._brush_mask[y0:y1, x0:x1] |= disc
        self.viewport().update()

    def _commit_brush(self) -> None:
        if self._brush_mask is None or self._brush_target is None:
            self._brush_mask = None
            self._brush_target = None
            return
        annotation = self._brush_target
        rle, bbox = encode_mask(self._brush_mask)
        annotation.mask_rle = rle
        annotation.mask_bbox = bbox
        annotation.coordinates = list(bbox) if bbox else []
        existing = self._items.get(annotation.id)
        if existing is None:
            self.annotation_created.emit(annotation)
        else:
            mask, mask_bbox = decode_mask(rle, bbox)
            existing.set_mask(mask, mask_bbox)
            self.annotation_edit_finished.emit(annotation.id)
        self._brush_mask = None
        self._brush_target = None
        self.viewport().update()

    # -- overlay painting ----------------------------------------------------

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        self._draw_pending(painter)
        self._draw_ruler(painter)
        self._draw_brush_preview(painter)
        painter.save()
        painter.resetTransform()
        self._draw_crosshair(painter)
        self._draw_orientation(painter)
        self._draw_magnifier(painter)
        painter.restore()

    def _draw_pending(self, painter: QPainter) -> None:
        if not self._pending_points:
            return
        try:
            colour = QColor(get_class(self.active_class).colour)
        except KeyError:
            colour = QColor(PALETTE.accent)
        pen = QPen(colour, 1.6, Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        geometry = self._geometry_for_active_class()
        points = list(self._pending_points)
        if self._cursor_in_view:
            points.append(self._cursor_scene)

        if geometry is GeometryType.BOX and len(points) >= 2:
            painter.drawRect(QRectF(points[0], points[-1]).normalized())
        elif geometry is GeometryType.POLYGON and len(points) >= 3:
            painter.drawPolygon(*[points])
        elif len(points) >= 2:
            painter.drawPolyline(*[points])

        marker = 3.0 / max(0.05, self.zoom_factor())
        painter.setBrush(colour)
        painter.setPen(Qt.NoPen)
        for p in self._pending_points:
            painter.drawEllipse(p, marker, marker)

    def _draw_ruler(self, painter: QPainter) -> None:
        if not self._ruler_points:
            return
        pen = QPen(QColor(PALETTE.warning), 1.8, Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        a = self._ruler_points[0]
        b = self._cursor_scene if self._cursor_in_view else a
        painter.drawLine(a, b)
        marker = 3.0 / max(0.05, self.zoom_factor())
        painter.setBrush(QColor(PALETTE.warning))
        painter.drawEllipse(a, marker, marker)

    def _draw_brush_preview(self, painter: QPainter) -> None:
        if self.active_tool not in (Tool.BRUSH, Tool.ERASER) or not self._cursor_in_view:
            return
        if self._brush_mask is not None:
            colour = QColor(PALETTE.accent)
            colour.setAlpha(90)
            ys, xs = np.nonzero(self._brush_mask)
            if len(xs):
                painter.setPen(Qt.NoPen)
                painter.setBrush(colour)
                x0, x1 = int(xs.min()), int(xs.max())
                y0, y1 = int(ys.min()), int(ys.max())
                sub = self._brush_mask[y0 : y1 + 1, x0 : x1 + 1]
                h, w = sub.shape
                rgba = np.zeros((h, w, 4), dtype=np.uint8)
                rgba[..., 0] = colour.red()
                rgba[..., 1] = colour.green()
                rgba[..., 2] = colour.blue()
                rgba[..., 3] = np.where(sub, 110, 0).astype(np.uint8)
                image = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
                painter.drawPixmap(QRectF(x0, y0, w, h), QPixmap.fromImage(image), QRectF(0, 0, w, h))
        pen = QPen(
            QColor(PALETTE.danger if self.active_tool == Tool.ERASER else PALETTE.accent), 1.4
        )
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(self._cursor_scene, self._brush_radius, self._brush_radius)

    def _draw_crosshair(self, painter: QPainter) -> None:
        if not self.crosshair_enabled or not self._cursor_in_view:
            return
        if self.active_tool in (Tool.BRUSH, Tool.ERASER):
            return
        position = self.mapFromScene(self._cursor_scene)
        rect = self.viewport().rect()
        pen = QPen(QColor(255, 255, 255, 70), 1.0)
        painter.setPen(pen)
        painter.drawLine(rect.left(), position.y(), rect.right(), position.y())
        painter.drawLine(position.x(), rect.top(), position.x(), rect.bottom())

    def _draw_orientation(self, painter: QPainter) -> None:
        """Label the anatomical sides on the image.

        A panoramic radiograph is displayed as if facing the patient, so the
        patient's right is on the viewer's left. Getting this wrong swaps every
        side specific measurement, so it is stated on the image rather than left
        to memory.
        """
        if self.image is None:
            return
        rect = self.viewport().rect()
        font = QFont()
        font.setPointSizeF(11.0)
        font.setBold(True)
        painter.setFont(font)

        # The marker sits over whatever the image shows at that edge, which on a
        # panoramic is often a bright band. A dark chip behind it keeps the
        # letter readable rather than leaving it to luck.
        for letter, box in (
            ("R", QRect(rect.left() + 8, rect.center().y() - 13, 26, 26)),
            ("L", QRect(rect.right() - 34, rect.center().y() - 13, 26, 26)),
        ):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 165))
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QPen(QColor(PALETTE.text)))
            painter.drawText(box, Qt.AlignCenter, letter)

    def _draw_magnifier(self, painter: QPainter) -> None:
        if not self.magnifier_enabled or not self._cursor_in_view or self.image is None:
            return
        size = 150
        rect = self.viewport().rect()
        position = self.mapFromScene(self._cursor_scene)
        x = rect.right() - size - 12
        y = rect.top() + 12
        if position.x() > x - 40 and position.y() < y + size + 40:
            x = rect.left() + 12

        target = QRect(x, y, size, size)
        half = size / (2.0 * self.magnifier_factor * max(0.05, self.zoom_factor()))
        source_scene = QRectF(
            self._cursor_scene.x() - half, self._cursor_scene.y() - half, half * 2, half * 2
        )

        painter.setPen(QPen(QColor(PALETTE.border_light), 1.0))
        painter.setBrush(QColor(PALETTE.canvas))
        painter.drawRect(target)
        if self._pixmap_item is not None:
            painter.save()
            painter.setClipRect(target)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawPixmap(target, self._pixmap_item.pixmap(), source_scene.toRect())
            painter.restore()
        painter.setPen(QPen(QColor(PALETTE.accent), 1.0))
        painter.drawLine(target.center().x(), target.top() + 4, target.center().x(), target.bottom() - 4)
        painter.drawLine(target.left() + 4, target.center().y(), target.right() - 4, target.center().y())
        painter.setPen(QPen(QColor(PALETTE.text_dim)))
        font = QFont()
        font.setPointSizeF(7.5)
        painter.setFont(font)
        painter.drawText(
            QRect(target.left(), target.bottom() + 2, size, 14),
            Qt.AlignRight, f"{self.magnifier_factor:g}x",
        )


def _nearest_segment(points, position) -> int:
    """Index of the polyline segment closest to a position."""
    from ...core.geometry import closest_point_on_polyline

    if len(points) < 2:
        return 0
    _, index, _ = closest_point_on_polyline(position, points)
    return index


# ---------------------------------------------------------------------------
# Mask encoding
# ---------------------------------------------------------------------------


def encode_mask(mask) -> tuple:
    """Run length encode a boolean mask, cropped to its bounding box.

    The encoding is column major over the cropped region, stored as alternating
    run lengths starting with a background run. It is compact, exact and plain
    enough to be decoded by a reader that only has the description.
    """
    if mask is None or not mask.any():
        return "", []
    ys, xs = np.nonzero(mask)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    cropped = mask[y0:y1, x0:x1]

    flat = cropped.ravel(order="F").astype(np.uint8)
    changes = np.flatnonzero(np.diff(flat)) + 1
    boundaries = np.concatenate(([0], changes, [flat.size]))
    runs = np.diff(boundaries)
    if flat[0] == 1:
        runs = np.concatenate(([0], runs))
    return " ".join(str(int(r)) for r in runs), [x0, y0, x1 - x0, y1 - y0]


def decode_mask(rle: str, bbox) -> tuple:
    """Decode a run length encoded mask back to a boolean array."""
    if not rle or not bbox or len(bbox) < 4:
        return None, bbox
    try:
        runs = [int(v) for v in rle.split()]
        width, height = int(bbox[2]), int(bbox[3])
    except (ValueError, TypeError, IndexError):
        return None, bbox
    total = width * height
    flat = np.zeros(total, dtype=bool)
    position = 0
    value = False
    for run in runs:
        end = min(total, position + run)
        if value and end > position:
            flat[position:end] = True
        position = end
        value = not value
        if position >= total:
            break
    return flat.reshape((height, width), order="F"), bbox
