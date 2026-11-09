"""Interactive page outline editor.

The photo shows four draggable corners and four draggable edges; an edge moves
parallel to itself, taking both of its corners along. While dragging, a loupe
shows the spot under the cursor magnified (from the full-resolution photo once
it is available), so the paper corner can be hit exactly.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QPolygonF, QResizeEvent
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QLabel,
    QWidget,
)

from paperscan.gui.images import to_qimage
from paperscan.imaging.geometry import CORNER_COUNT, Point, Quad

#: Handle radius in screen pixels (independent of zoom).
HANDLE_RADIUS = 11
#: Width of the invisible grab area along an edge, in screen pixels.
EDGE_GRAB_WIDTH = 16
OUTLINE_COLOR = QColor("#2f7de1")
HANDLE_FILL = QColor(47, 125, 225, 90)
EDGE_HOVER_COLOR = QColor(47, 125, 225, 110)
SHADE_COLOR = QColor(0, 0, 0, 70)
OUTLINE_WIDTH = 2
MIN_EDITOR_WIDTH = 300
#: Loupe size on screen and how many detail-image pixels it shows across.
LOUPE_SIZE = 200
LOUPE_SPAN_PIXELS = 100
LOUPE_MARGIN = 12
LOUPE_BORDER = QColor("#ffffff")
CROSSHAIR_COLOR = QColor("#ff3b30")


class _CornerHandle(QGraphicsEllipseItem):
    """A draggable corner; reports moves to the editor."""

    def __init__(self, editor: CornerEditor) -> None:
        super().__init__(-HANDLE_RADIUS, -HANDLE_RADIUS, 2 * HANDLE_RADIUS, 2 * HANDLE_RADIUS)
        self._editor = editor
        self.setBrush(QBrush(HANDLE_FILL))
        self.setPen(QPen(OUTLINE_COLOR, OUTLINE_WIDTH))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(3)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:  # noqa: N802 - Qt API
        """Keep the handle inside the photo and update outline and loupe while dragging."""
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and isinstance(value, QPointF):
            return self._editor.clamp_to_image(value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._editor.geometry_changed()
            if self.isUnderMouse() and self._editor.dragging:
                self._editor.show_loupe(self.pos())
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802 - Qt API
        """Start a drag with the loupe."""
        super().mousePressEvent(event)
        self._editor.drag_started(self.pos())

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802 - Qt API
        """Publish the final outline once the drag ends."""
        super().mouseReleaseEvent(event)
        self._editor.drag_finished()


class _EdgeHandle(QGraphicsPathItem):
    """An invisible, wide grab area along one edge; dragging moves the edge parallel to itself."""

    def __init__(self, editor: CornerEditor, first: int, second: int) -> None:
        super().__init__()
        self._editor = editor
        self._corners = (first, second)
        self._press_scene = QPointF()
        self._press_positions: tuple[QPointF, QPointF] = (QPointF(), QPointF())
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setAcceptHoverEvents(True)
        self.setZValue(2)

    def update_shape(self, start: QPointF, end: QPointF, scale: float) -> None:
        """Rebuild the grab area between the two corners (``scale``: scene units per screen pixel)."""
        line = QPainterPath(start)
        line.lineTo(end)
        stroker = QPainterPathStroker()
        stroker.setWidth(EDGE_GRAB_WIDTH * scale)
        self.setPath(stroker.createStroke(line))
        horizontal = abs(end.x() - start.x()) >= abs(end.y() - start.y())
        self.setCursor(Qt.CursorShape.SizeVerCursor if horizontal else Qt.CursorShape.SizeHorCursor)

    def hoverEnterEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        """Highlight the edge under the mouse."""
        self.setBrush(QBrush(EDGE_HOVER_COLOR))

    def hoverLeaveEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        """Remove the highlight."""
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802 - Qt API
        """Remember where the drag started."""
        self._press_scene = event.scenePos()
        self._press_positions = self._editor.corner_positions(self._corners)
        self._editor.drag_started(event.scenePos())
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802 - Qt API
        """Shift both corners along the edge normal by the mouse movement."""
        delta = event.scenePos() - self._press_scene
        start, end = self._press_positions
        direction = end - start
        length = (direction.x() ** 2 + direction.y() ** 2) ** 0.5 or 1.0
        normal = QPointF(-direction.y() / length, direction.x() / length)
        distance = delta.x() * normal.x() + delta.y() * normal.y()
        shift = normal * distance
        self._editor.move_corners(self._corners, (start + shift, end + shift))
        self._editor.show_loupe(event.scenePos())

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802 - Qt API
        """Publish the final outline."""
        self._editor.drag_finished()


class CornerEditor(QGraphicsView):
    """Shows the preview photo and lets the user drag corners and edges of the page outline.

    Coordinates in the scene are preview pixels; :attr:`quad_changed` reports
    the outline in preview pixels, the caller converts to source pixels.
    """

    quad_changed = Signal(object)  # Quad in preview pixel coordinates

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty editor."""
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setMinimumWidth(MIN_EDITOR_WIDTH)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._image_rect = QRectF()
        self._corners: list[_CornerHandle] = []
        self._edges: list[_EdgeHandle] = []
        self._outline: QGraphicsPolygonItem | None = None
        self._updating = False
        self.dragging = False
        self._preview_image = QImage()
        self._detail_image = QImage()
        self._detail_scale = 1.0
        self._loupe = QLabel(self.viewport())
        self._loupe.setFixedSize(LOUPE_SIZE, LOUPE_SIZE)
        self._loupe.hide()

    # --- Page -----------------------------------------------------------------------------------

    def show_page(self, rgb: np.ndarray, quad: Quad) -> None:
        """Display ``rgb`` with the outline ``quad`` (preview pixel coordinates)."""
        scene = self.scene()
        scene.clear()
        self._preview_image = to_qimage(rgb)
        self._detail_image = QImage()
        pixmap_item = scene.addPixmap(QPixmap.fromImage(self._preview_image))
        self._image_rect = pixmap_item.boundingRect()
        scene.setSceneRect(self._image_rect)
        self._outline = scene.addPolygon(QPolygonF(), QPen(OUTLINE_COLOR, OUTLINE_WIDTH), QBrush(SHADE_COLOR))
        self._outline.setZValue(1)
        self._corners = [_CornerHandle(self) for _ in range(CORNER_COUNT)]
        self._edges = [_EdgeHandle(self, index, (index + 1) % CORNER_COUNT) for index in range(CORNER_COUNT)]
        for item in [*self._edges, *self._corners]:
            scene.addItem(item)
        self.set_quad(quad)
        self._fit()

    def set_detail_image(self, rgb: np.ndarray, scale: float) -> None:
        """Use the full-resolution photo for the loupe; ``scale`` = detail pixels per preview pixel."""
        self._detail_image = to_qimage(rgb)
        self._detail_scale = scale

    def clear_page(self) -> None:
        """Show nothing."""
        self.scene().clear()
        self._corners, self._edges = [], []
        self._outline = None
        self._loupe.hide()

    def set_quad(self, quad: Quad) -> None:
        """Move the handles to ``quad`` without emitting :attr:`quad_changed`."""
        self._updating = True
        for handle, point in zip(self._corners, quad.points, strict=True):
            handle.setPos(QPointF(point.x, point.y))
        self._updating = False
        self.geometry_changed()

    def current_quad(self) -> Quad:
        """Return the outline in preview pixel coordinates."""
        return Quad(*(Point(handle.pos().x(), handle.pos().y()) for handle in self._corners))

    # --- Called by the handles ------------------------------------------------------------------

    def clamp_to_image(self, position: QPointF) -> QPointF:
        """Return ``position`` limited to the photo area."""
        rect = self._image_rect
        return QPointF(min(max(position.x(), rect.left()), rect.right()), min(max(position.y(), rect.top()), rect.bottom()))

    def corner_positions(self, corners: tuple[int, int]) -> tuple[QPointF, QPointF]:
        """Return the positions of two corners."""
        return self._corners[corners[0]].pos(), self._corners[corners[1]].pos()

    def move_corners(self, corners: tuple[int, int], positions: tuple[QPointF, QPointF]) -> None:
        """Move two corners at once (used by edge drags)."""
        for index, position in zip(corners, positions, strict=True):
            self._corners[index].setPos(self.clamp_to_image(position))

    def geometry_changed(self) -> None:
        """Redraw the outline polygon and the edge grab areas."""
        if self._outline is None or not self._corners:
            return
        positions = [handle.pos() for handle in self._corners]
        self._outline.setPolygon(QPolygonF(positions))
        scale = 1 / self.transform().m11() if self.transform().m11() else 1.0
        for edge, index in zip(self._edges, range(CORNER_COUNT), strict=True):
            edge.update_shape(positions[index], positions[(index + 1) % CORNER_COUNT], scale)

    def drag_started(self, position: QPointF) -> None:
        """Show the loupe for the drag."""
        self.dragging = True
        self.show_loupe(position)

    def drag_finished(self) -> None:
        """Hide the loupe and emit the edited outline."""
        self.dragging = False
        self._loupe.hide()
        if not self._updating and self._corners:
            self.quad_changed.emit(self.current_quad())

    # --- Loupe ----------------------------------------------------------------------------------

    def show_loupe(self, scene_position: QPointF) -> None:
        """Show the magnified surroundings of ``scene_position`` in the corner away from it."""
        crop = self._loupe_crop(scene_position)
        if crop.isNull():
            return
        pixmap = QPixmap.fromImage(
            crop.scaled(LOUPE_SIZE, LOUPE_SIZE, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )
        self._draw_crosshair(pixmap)
        self._loupe.setPixmap(pixmap)
        self._loupe.move(self._loupe_corner(self.mapFromScene(scene_position)))
        self._loupe.show()
        self._loupe.raise_()

    def _loupe_crop(self, scene_position: QPointF) -> QImage:
        """Cut the area around the point from the detail image (or the preview while it loads)."""
        use_detail = not self._detail_image.isNull()
        image = self._detail_image if use_detail else self._preview_image
        scale = self._detail_scale if use_detail else 1.0
        span = LOUPE_SPAN_PIXELS if use_detail else max(1, round(LOUPE_SPAN_PIXELS / max(self._detail_scale, 1.0)))
        center_x, center_y = round(scene_position.x() * scale), round(scene_position.y() * scale)
        return image.copy(center_x - span // 2, center_y - span // 2, span, span)

    def _draw_crosshair(self, pixmap: QPixmap) -> None:
        """Mark the exact point and frame the loupe."""
        painter = QPainter(pixmap)
        middle = LOUPE_SIZE / 2
        painter.setPen(QPen(CROSSHAIR_COLOR, 1))
        painter.drawLine(QPointF(middle, 0), QPointF(middle, LOUPE_SIZE))
        painter.drawLine(QPointF(0, middle), QPointF(LOUPE_SIZE, middle))
        painter.setPen(QPen(LOUPE_BORDER, 3))
        painter.drawRect(QRectF(1, 1, LOUPE_SIZE - 2, LOUPE_SIZE - 2))
        painter.end()

    def _loupe_corner(self, cursor: QPoint) -> QPoint:
        """Return the loupe position in the viewport corner diagonally opposite the cursor."""
        viewport = self.viewport().rect()
        left = viewport.right() - LOUPE_SIZE - LOUPE_MARGIN if cursor.x() < viewport.center().x() else LOUPE_MARGIN
        top = viewport.bottom() - LOUPE_SIZE - LOUPE_MARGIN if cursor.y() < viewport.center().y() else LOUPE_MARGIN
        return QPoint(left, top)

    # --- View -----------------------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        """Keep the photo fitted to the view."""
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        """Scale the scene so the whole photo is visible, then resize the edge grab areas."""
        if not self._image_rect.isEmpty():
            self.fitInView(self._image_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self.geometry_changed()
