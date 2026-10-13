"""Pan/zoom image viewer. Mask previews retain their native pixel resolution."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


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
