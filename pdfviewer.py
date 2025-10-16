import sys
import json
import fitz  # PyMuPDF
from PyQt5.QtWidgets import (
    QApplication, QLabel, QScrollArea, QMainWindow, QFileDialog, QToolBar, QAction
)
from PyQt5.QtGui import QPixmap, QImage, QPainter, QPen, QIcon
from PyQt5.QtCore import Qt, QTimer


class PDFViewer(QMainWindow):
    def __init__(self, pdf_path):
        super().__init__()
        self.setWindowTitle("Smooth PDF Viewer – Anchors, Zoom, Width Fit")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

        # PDF rendering data
        self.doc = fitz.open(pdf_path)
        self.zoom = 1.0  # current zoom multiplier
        self.fit_to_width = True

        # Anchors + navigation
        self.anchors = []
        self.current_anchor_index = -1

        # Render base image (full resolution)
        self.base_image = self.render_pdf_to_long_image(self.doc)
        self.image = self.base_image

        # UI elements
        self.label = ClickableLabel(self)
        self.label.setPixmap(QPixmap.fromImage(self.image))
        self.label.setFocusPolicy(Qt.StrongFocus)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.label)
        self.scroll_area.setWidgetResizable(True)
        self.setCentralWidget(self.scroll_area)

        # Toolbar for zoom/reset
        self.toolbar = QToolBar("Tools")
        self.addToolBar(self.toolbar)

        act_fit = QAction(QIcon(), "Fit Width", self)
        act_fit.triggered.connect(self.reset_zoom)
        self.toolbar.addAction(act_fit)

        # Timer for smooth scroll
        self.timer = QTimer()
        self.timer.timeout.connect(self.smooth_scroll)
        self.target_y = 0

        # Draw anchors
        self.update_pixmap()

    # ---------- PDF Rendering ----------
    def render_pdf_to_long_image(self, doc):
        """Render all pages into one tall QImage at base zoom (2.0)."""
        zoom = 2.0
        mat = fitz.Matrix(zoom, zoom)
        images = []
        widths, heights = [], []

        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            images.append(img.copy())
            widths.append(pix.width)
            heights.append(pix.height)

        total_height = sum(heights)
        total_width = max(widths)
        long_image = QImage(total_width, total_height, QImage.Format_RGB888)
        long_image.fill(Qt.white)

        painter = QPainter(long_image)
        y_offset = 0
        for img in images:
            painter.drawImage(0, y_offset, img)
            y_offset += img.height()
        painter.end()

        return long_image

    # ---------- Scaling and Updating ----------
    def update_scaled_image(self):
        """Apply zoom or fit-to-width scaling."""
        if self.fit_to_width:
            target_width = self.scroll_area.viewport().width()
            self.zoom = target_width / self.base_image.width()
        scaled_width = int(self.base_image.width() * self.zoom)
        scaled_height = int(self.base_image.height() * self.zoom)
        self.image = self.base_image.scaled(
            scaled_width, scaled_height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        self.update_pixmap()

    def update_pixmap(self):
        """Redraw anchors on top of scaled image and refresh immediately."""
        pixmap = QPixmap.fromImage(self.image.copy())
        painter = QPainter(pixmap)

        # only draw anchors if not in performance mode
        if not getattr(self, "performance_mode", False):
            pen = QPen(Qt.red, 3)
            painter.setPen(pen)
            for y in self.anchors:
                painter.drawLine(0, int(y * self.zoom), pixmap.width(), int(y * self.zoom))

        painter.end()
        self.label.setPixmap(pixmap)
        self.label.adjustSize()
        self.label.update()
        self.scroll_area.viewport().update()
        QApplication.processEvents()




    def resizeEvent(self, event):
        """Auto-rescale when in fit-to-width mode."""
        super().resizeEvent(event)
        if self.fit_to_width:
            self.update_scaled_image()

    # ---------- Anchor Management ----------
    def add_anchor(self, y_click):
        """Add anchor at correct Y coordinate (accounting for zoom).
        IMPORTANT: event.pos().y() is already in the label's content coords,
        so don't add the scrollbar value again.
        """
        # y_click is already relative to the full label (scaled) coordinates
        y_abs = y_click / self.zoom  # convert back to base-image coords
        # clamp just in case
        y_abs = max(0, min(self.base_image.height() - 1, y_abs))
        self.anchors.append(y_abs)
        self.anchors = sorted(list(set(self.anchors)))
        print(f"Added anchor at base-y={y_abs:.1f} (click scaled-y={y_click})")
        self.update_pixmap()

    def remove_nearest_anchor(self, y_click):
        """Remove the anchor nearest to the clicked Y position (converted from scaled coords)."""
        if not self.anchors:
            return
        y_abs = y_click / self.zoom
        nearest = min(self.anchors, key=lambda a: abs(a - y_abs))
        self.anchors.remove(nearest)
        print(f"Removed anchor near base-y={y_abs:.1f} (actual {nearest:.1f})")
        self.update_pixmap()


    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Right:
            self.next_anchor()
        elif key == Qt.Key_Left:
            self.prev_anchor()
        elif key == Qt.Key_S:
            self.save_anchors()
        elif key == Qt.Key_L:
            self.load_anchors()
        elif key == Qt.Key_Plus or key == Qt.Key_Equal:
            self.zoom_in()
        elif key == Qt.Key_Minus:
            self.zoom_out()
        elif key == Qt.Key_F:
            self.enter_performance_mode()
        elif key == Qt.Key_Escape:
            if getattr(self, "performance_mode", False):
                self.exit_performance_mode()


    def next_anchor(self):
        if not self.anchors:
            print("No anchors set!")
            return
        if self.current_anchor_index < len(self.anchors) - 1:
            self.current_anchor_index += 1
        else:
            self.current_anchor_index = 0
        self.scroll_to_anchor(self.current_anchor_index)

    def prev_anchor(self):
        if not self.anchors:
            print("No anchors set!")
            return
        if self.current_anchor_index > 0:
            self.current_anchor_index -= 1
        else:
            self.current_anchor_index = len(self.anchors) - 1
        self.scroll_to_anchor(self.current_anchor_index)

    def scroll_to_anchor(self, index):
        target_y = int(self.anchors[index] * self.zoom)
        sb = self.scroll_area.verticalScrollBar()
        self.start_y = sb.value()
        self.target_y = min(max(0, target_y), sb.maximum())

        # Fewer frames = faster travel; higher speed = snappier feel
        frames = 30            # ↓ fewer frames (was 20)
        duration_ms = 200      # total animation time
        interval = duration_ms / frames
        self.speed = (self.target_y - self.start_y) / frames

        self.timer.start(int(interval))
        print(f"Scrolling quickly to anchor {index}: {target_y/self.zoom:.1f} (zoom={self.zoom:.2f})")

    def smooth_scroll(self):
        sb = self.scroll_area.verticalScrollBar()
        new_val = sb.value() + self.speed
        if (self.speed > 0 and new_val >= self.target_y) or (self.speed < 0 and new_val <= self.target_y):
            sb.setValue(int(self.target_y))
            self.timer.stop()
        else:
            sb.setValue(int(new_val))

    # ---------- Zoom ----------
    def zoom_in(self):
        self.fit_to_width = False
        self.zoom *= 1.25
        print(f"Zoom In → {self.zoom:.2f}")
        self.update_scaled_image()

    def zoom_out(self):
        self.fit_to_width = False
        self.zoom /= 1.25
        print(f"Zoom Out → {self.zoom:.2f}")
        self.update_scaled_image()

    def reset_zoom(self):
        """Fit to window width."""
        self.fit_to_width = True
        self.update_scaled_image()
        print("Fit-to-width mode enabled")

    # ---------- Save/Load ----------
    def save_anchors(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Anchors", "", "JSON Files (*.json)")
        if not path:
            return
        with open(path, "w") as f:
            json.dump(self.anchors, f, indent=2)
        print(f"Anchors saved to {path}")

    def load_anchors(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Anchors", "", "JSON Files (*.json)")
        if not path:
            return
        with open(path, "r") as f:
            self.anchors = json.load(f)
        self.current_anchor_index = -1
        print(f"Loaded {len(self.anchors)} anchors from {path}")
        self.update_pixmap()

    # ---------- Performance Mode ----------
    def enter_performance_mode(self):
        """Hide anchors and toolbar, go fullscreen."""
        self.performance_mode = True
        self.showFullScreen()
        if hasattr(self, "toolbar"):
            self.toolbar.setVisible(False)
        self.update_pixmap()  # redraw without anchors
        print("🎬 Entered Performance Mode (press ESC to exit)")

    def exit_performance_mode(self):
        """Show anchors and toolbar again."""
        self.performance_mode = False
        self.showMaximized()
        if hasattr(self, "toolbar"):
            self.toolbar.setVisible(True)
        self.update_pixmap()
        print("⬅️  Exited Performance Mode")




class ClickableLabel(QLabel):
    """Clickable PDF image that handles mouse and key events."""
    def __init__(self, parent_viewer):
        super().__init__()
        self.parent_viewer = parent_viewer
        self.setFocusPolicy(Qt.StrongFocus)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.parent_viewer.add_anchor(event.pos().y())
        elif event.button() == Qt.RightButton:
            self.parent_viewer.remove_nearest_anchor(event.pos().y())
        self.setFocus()

    def keyPressEvent(self, event):
        self.parent_viewer.keyPressEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = PDFViewer("example.pdf")  # 👈 Replace with your PDF
    viewer.showMaximized()
    sys.exit(app.exec_())
