"""Pan/zoom image viewer. Mask previews retain their native pixel resolution."""

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)


class ZoomCanvas(QGraphicsView):
    def __init__(self, parent=None, checker=False):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.fit_mode = True
        self.has_image = False
        if checker:
            tile = QPixmap(24, 24)
            tile.fill(QColor("#666666"))
            painter = QPainter(tile)
            painter.fillRect(0, 0, 12, 12, QColor("#888888"))
            painter.fillRect(12, 12, 12, 12, QColor("#888888"))
            painter.end()
            self.setBackgroundBrush(QBrush(tile))
        else:
            self.setBackgroundBrush(QColor("#181818"))

    def set_image(self, image):
        self.scene().clear()
        self.has_image = image is not None and not image.isNull()
        if self.has_image:
            item = self.scene().addPixmap(QPixmap.fromImage(image))
            self.scene().setSceneRect(item.boundingRect())
            self.fit_image()
        else:
            self.scene().setSceneRect(0, 0, 1, 1)

    def fit_image(self):
        self.fit_mode = True
        if self.has_image:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def actual_size(self):
        self.fit_mode = False
        self.resetTransform()

    def zoom(self, factor):
        if not self.has_image:
            return
        self.fit_mode = False
        scale = self.transform().m11() * factor
        if 0.01 <= scale <= 32:
            self.scale(factor, factor)

    def wheelEvent(self, event):
        self.zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit_image()


class ImageView(QWidget):
    def __init__(self, title, parent=None, checker=False):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.title = QLabel(title)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.canvas = ZoomCanvas(self, checker)
        self.canvas.setAccessibleName(title + ". Mouse wheel to zoom, drag to pan.")
        self.canvas.setMinimumSize(100, 140)
        layout.addWidget(self.canvas, 1)
        controls = QHBoxLayout()
        for text, callback in (("Fit", self.canvas.fit_image), ("100%", self.canvas.actual_size),
                               ("−", lambda: self.canvas.zoom(1 / 1.2)), ("+", lambda: self.canvas.zoom(1.2))):
            button = QPushButton(text)
            button.setAccessibleName(f"{title}: {text}")
            button.setMaximumWidth(58)
            button.clicked.connect(callback)
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)

    def set_image(self, image):
        self.canvas.set_image(image)


class MaskPaintCanvas(ZoomCanvas):
    changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.original = None
        self.mask = None
        self.keep = True
        self.brush_size = 40
        self.undo_stack = []
        self.last_point = None
        self.setDragMode(QGraphicsView.NoDrag)
        self.setAccessibleName("Editable alpha mask. Red is removed; unshaded image is kept.")

    def set_images(self, original, mask):
        self.original = original.copy() if original is not None and not original.isNull() else None
        self.mask = (mask.convertToFormat(QImage.Format_Grayscale8).copy()
                     if mask is not None and not mask.isNull() else None)
        self.undo_stack.clear()
        self.last_point = None
        self.render_overlay()
        self.changed.emit(False)

    def set_keep(self, keep):
        self.keep = bool(keep)

    def set_brush_size(self, size):
        self.brush_size = int(size)

    def push_undo(self):
        if self.mask is not None:
            self.undo_stack.append(self.mask.copy())
            self.undo_stack = self.undo_stack[-20:]

    def undo(self):
        if self.undo_stack:
            self.mask = self.undo_stack.pop()
            self.render_overlay()
            self.changed.emit(True)

    def image_point(self, event):
        if self.mask is None or not self.has_image:
            return None
        point = self.mapToScene(event.position().toPoint())
        rect = self.sceneRect()
        if not rect.contains(point):
            return None
        display_width = self.original.width() if self.original is not None else self.mask.width()
        display_height = self.original.height() if self.original is not None else self.mask.height()
        return (point.x() * self.mask.width() / max(1, display_width),
                point.y() * self.mask.height() / max(1, display_height))

    def paint_segment(self, start, end):
        if self.mask is None:
            return
        painter = QPainter(self.mask)
        color = QColor(255, 255, 255) if self.keep else QColor(0, 0, 0)
        pen = QPen(color, self.brush_size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(int(start[0]), int(start[1]), int(end[0]), int(end[1]))
        painter.end()
        self.render_overlay(keep_transform=True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            point = self.image_point(event)
            if point is not None:
                self.push_undo()
                self.last_point = point
                self.paint_segment(point, point)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.last_point is not None and event.buttons() & Qt.LeftButton:
            point = self.image_point(event)
            if point is not None:
                self.paint_segment(self.last_point, point)
                self.last_point = point
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.last_point is not None:
            self.last_point = None
            self.changed.emit(True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def render_overlay(self, keep_transform=False):
        previous = self.transform()
        if self.mask is None:
            self.set_image(None)
            return
        if self.original is None:
            display = self.mask
        else:
            display = self.original.convertToFormat(QImage.Format_RGBA8888).copy()
            scaled = self.mask.scaled(display.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            removed = scaled.copy()
            removed.invertPixels()
            overlay = QImage(display.size(), QImage.Format_RGBA8888)
            overlay.fill(QColor(228, 87, 87, 255))
            overlay.setAlphaChannel(removed)
            painter = QPainter(display)
            painter.setOpacity(0.46)
            painter.drawImage(0, 0, overlay)
            painter.end()
        self.set_image(display)
        if keep_transform:
            self.setTransform(previous)
            self.fit_mode = False

    def mask_png(self):
        if self.mask is None:
            return b""
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        if not self.mask.save(buffer, "PNG"):
            raise ValueError("Could not encode the edited mask.")
        buffer.close()
        return bytes(data)


class MaskEditor(QWidget):
    changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        note = QLabel("Paint Keep to restore hair or clothing. Paint Remove to clear remaining background. Red areas are transparent.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.canvas = MaskPaintCanvas(self)
        self.canvas.setMinimumHeight(260)
        self.canvas.changed.connect(self.changed)
        layout.addWidget(self.canvas, 1)
        controls = QHBoxLayout()
        group = QButtonGroup(self)
        self.keep = QPushButton("Keep subject")
        self.remove = QPushButton("Remove background")
        for button in (self.keep, self.remove):
            button.setCheckable(True)
            group.addButton(button)
            controls.addWidget(button)
        self.keep.setChecked(True)
        self.keep.toggled.connect(lambda checked: self.canvas.set_keep(True) if checked else None)
        self.remove.toggled.connect(lambda checked: self.canvas.set_keep(False) if checked else None)
        controls.addWidget(QLabel("Brush"))
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(4, 240)
        self.size.setValue(40)
        self.size.setMaximumWidth(180)
        self.size.valueChanged.connect(self.canvas.set_brush_size)
        controls.addWidget(self.size)
        undo = QPushButton("Undo stroke")
        undo.clicked.connect(self.canvas.undo)
        controls.addWidget(undo)
        for text, callback in (("Fit", self.canvas.fit_image), ("100%", self.canvas.actual_size),
                               ("−", lambda: self.canvas.zoom(1 / 1.2)), ("+", lambda: self.canvas.zoom(1.2))):
            button = QPushButton(text)
            button.setMaximumWidth(62)
            button.clicked.connect(callback)
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)

    def set_images(self, original, mask):
        self.canvas.set_images(original, mask)

    def mask_png(self):
        return self.canvas.mask_png()


class PortraitLayoutCanvas(QGraphicsView):
    transform_changed = Signal(float, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.image_item = None
        self.handle = None
        self.canvas_width = 1080
        self.canvas_height = 1080
        self.padding_percent = 4.0
        self.subject_scale = 100.0
        self.offset_x = 0
        self.offset_y = 0
        self.base_scale = 1.0
        self.drag_mode = None
        self.drag_start = None
        self.start_position = None
        self.fit_mode = True
        self.setAccessibleName("Portrait layout canvas. Drag the portrait to move it; drag the square handle to resize it.")

    def set_portrait(self, image, settings):
        self.scene().clear()
        self.image_item = None
        self.handle = None
        self.canvas_width = settings.canvas_width
        self.canvas_height = settings.canvas_height
        self.padding_percent = settings.padding_percent
        self.subject_scale = settings.subject_scale
        self.offset_x = settings.offset_x
        self.offset_y = settings.offset_y
        self.scene().setSceneRect(0, 0, self.canvas_width, self.canvas_height)
        self.scene().addRect(self.sceneRect(), QPen(Qt.NoPen), QBrush(QColor(*settings.background_rgb())))
        if image is not None and not image.isNull():
            self.image_item = self.scene().addPixmap(QPixmap.fromImage(image))
            available_width = self.canvas_width * (1 - 2 * self.padding_percent / 100)
            available_height = self.canvas_height * (1 - 2 * self.padding_percent / 100)
            self.base_scale = min(available_width / image.width(), available_height / image.height())
            self.image_item.setScale(self.base_scale * self.subject_scale / 100)
            rect = self.image_item.sceneBoundingRect()
            self.image_item.setPos((self.canvas_width - rect.width()) / 2 + self.offset_x,
                                   (self.canvas_height - rect.height()) / 2 + self.offset_y)
            self.handle = self.scene().addRect(0, 0, 32, 32, QPen(QColor("#202020"), 2), QBrush(QColor("#eeeeee")))
            self.handle.setZValue(5)
            self.update_handle()
        self.fit_canvas()

    def update_handle(self):
        if self.image_item is not None and self.handle is not None:
            corner = self.image_item.sceneBoundingRect().bottomRight()
            size = max(18, min(self.canvas_width, self.canvas_height) * 0.03)
            self.handle.setRect(corner.x() - size, corner.y() - size, size, size)

    def fit_canvas(self):
        self.fit_mode = True
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def actual_size(self):
        self.fit_mode = False
        self.resetTransform()

    def wheelEvent(self, event):
        self.fit_mode = False
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        scale = self.transform().m11() * factor
        if 0.05 <= scale <= 16:
            self.scale(factor, factor)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit_canvas()

    def mousePressEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        if event.button() == Qt.LeftButton and self.handle is not None and self.handle.rect().contains(point):
            self.drag_mode = "resize"
        elif event.button() == Qt.LeftButton and self.image_item is not None and self.image_item.sceneBoundingRect().contains(point):
            self.drag_mode = "move"
        else:
            return super().mousePressEvent(event)
        self.drag_start = point
        self.start_position = self.image_item.pos()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.drag_mode or self.image_item is None:
            return super().mouseMoveEvent(event)
        point = self.mapToScene(event.position().toPoint())
        if self.drag_mode == "move":
            delta = point - self.drag_start
            self.image_item.setPos(self.start_position + delta)
        else:
            top_left = self.image_item.sceneBoundingRect().topLeft()
            target_width = max(20.0, point.x() - top_left.x())
            scale = target_width / max(1, self.image_item.pixmap().width())
            self.image_item.setScale(scale)
        self.update_handle()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag_mode and self.image_item is not None:
            rect = self.image_item.sceneBoundingRect()
            self.subject_scale = self.image_item.scale() / max(0.0001, self.base_scale) * 100
            self.offset_x = round(rect.center().x() - self.canvas_width / 2)
            self.offset_y = round(rect.center().y() - self.canvas_height / 2)
            self.drag_mode = None
            self.transform_changed.emit(self.subject_scale, self.offset_x, self.offset_y)
            event.accept()
            return
        super().mouseReleaseEvent(event)
