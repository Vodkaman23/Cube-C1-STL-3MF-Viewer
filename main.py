"""
CubeVi C1 STL Viewer
Main application with dual-window architecture:
  - Control window on primary monitor (drag-drop, rotation, zoom, preview)
  - Fullscreen output window on C1 display (pixel-perfect interlaced image)
"""

import sys
import os
import math
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QSlider, QFrame, QComboBox, QPushButton, QFileDialog,
    QSizePolicy, QGroupBox, QToolButton
)
from PyQt5.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QPainter, QFont, QColor, QPalette, QIcon, QDragEnterEvent, QDropEvent
import moderngl
from renderer import CubeViRenderer
from settings import load_settings, save_settings
from log import setup_logging, redirect_stdio


# ─── Shared dark stylesheet ───────────────────────────────────────────────────
DARK_STYLE = """
QWidget {
    background-color: #1e1e1e;
    color: #d4d4d4;
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 12px;
}
QGroupBox {
    background-color: #262626;
    border: 1px solid #3a3a3a;
    border-radius: 6px;
    margin-top: 14px;
    padding: 12px 10px 8px 10px;
    font-weight: 600;
    font-size: 11px;
    color: #b0b0b0;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: #8cb4d4;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #3a3a3a;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #5a9fd4;
    border: none;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover {
    background: #6db3e8;
}
QSlider::sub-page:horizontal {
    background: #3d6e8e;
    border-radius: 2px;
}
QComboBox {
    background-color: #2d2d2d;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 3px 8px;
    color: #d4d4d4;
    min-height: 20px;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #888;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background-color: #2d2d2d;
    border: 1px solid #3a3a3a;
    selection-background-color: #3d6e8e;
    color: #d4d4d4;
}
QPushButton {
    background-color: #2d2d2d;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 5px 14px;
    color: #d4d4d4;
    font-size: 11px;
}
QPushButton:hover {
    background-color: #353535;
    border-color: #5a9fd4;
}
QPushButton:pressed {
    background-color: #3d6e8e;
}
QToolTip {
    background-color: #2a2a2a;
    color: #d4d4d4;
    border: 1px solid #3a3a3a;
    padding: 4px;
    font-size: 11px;
}
"""


class CubeViOutputWindow(QWidget):
    """
    Fullscreen output window for the CubeVi C1 display.
    Displays the interlaced image at exact 1:1 pixel mapping.
    No scaling, no decorations, no margins - just raw pixels.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CubeVi C1 Output")
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.Tool  # Hides from taskbar
        )
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setStyleSheet("background-color: black;")
        self._pixmap = None

    def place_on_screen(self, screen):
        """Position this window to exactly cover the given screen"""
        geo = screen.geometry()
        self.show()
        self.move(geo.x(), geo.y())
        self.setFixedSize(geo.width(), geo.height())

    def set_image(self, qimage):
        """Update the displayed image (must be exactly 1440x2560)"""
        self._pixmap = QPixmap.fromImage(qimage)
        self.update()

    def paintEvent(self, event):
        if self._pixmap is not None:
            painter = QPainter(self)
            painter.drawPixmap(0, 0, self._pixmap)
            painter.end()


class DropZone(QLabel):
    """Drag-and-drop zone with visual feedback."""
    fileDropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._set_idle_style()
        self._show_empty()

    def _set_idle_style(self):
        self.setStyleSheet("""
            QLabel {
                background-color: #141414;
                border: 2px dashed #333;
                border-radius: 8px;
                color: #555;
                font-size: 13px;
                padding: 16px;
            }
        """)

    def _set_hover_style(self):
        self.setStyleSheet("""
            QLabel {
                background-color: #1a2233;
                border: 2px dashed #5a9fd4;
                border-radius: 8px;
                color: #7ab8e0;
                font-size: 13px;
                padding: 16px;
            }
        """)

    def _set_loaded_style(self):
        self.setStyleSheet("""
            QLabel {
                background-color: #000;
                border: none;
                border-radius: 0px;
            }
        """)

    def _show_empty(self):
        self._set_idle_style()
        self.setText("Drop STL / 3MF here\n\n"
                     "Left drag = rotate  |  Right drag = pan\n"
                     "Scroll = zoom  |  Shift = precision mode\n"
                     "Middle-click or R = reset  |  S = save\n"
                     "F = toggle C1  |  Esc = quit")

    def show_loaded(self, filename):
        self._set_loaded_style()

    def show_error(self, filename):
        self._set_idle_style()
        self.setStyleSheet(self.styleSheet().replace("color: #555", "color: #d45a5a"))
        self.setText(f"Failed to load:\n{filename}")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if len(urls) == 1 and urls[0].toLocalFile().lower().endswith(('.stl', '.3mf')):
                self._set_hover_style()
                event.accept()
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        if self.pixmap() and not self.pixmap().isNull():
            self._set_loaded_style()
        else:
            self._set_idle_style()

    def dropEvent(self, event: QDropEvent):
        filepath = event.mimeData().urls()[0].toLocalFile()
        self._set_idle_style()
        self.fileDropped.emit(filepath)


class CollapsibleSection(QWidget):
    """A section with a clickable header that toggles visibility of its content."""
    def __init__(self, title, parent=None, collapsed=False):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Header button
        self._toggle_btn = QPushButton(f"  {title}")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(not collapsed)
        self._toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2a2a;
                border: none;
                border-bottom: 1px solid #333;
                border-radius: 0px;
                padding: 6px 10px;
                text-align: left;
                font-weight: 600;
                font-size: 11px;
                color: #8cb4d4;
            }
            QPushButton:hover {
                background-color: #303030;
            }
        """)
        self._toggle_btn.clicked.connect(self._on_toggle)
        self._layout.addWidget(self._toggle_btn)

        # Content area
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(10, 6, 10, 8)
        self._content_layout.setSpacing(5)
        self._layout.addWidget(self._content)

        self._content.setVisible(not collapsed)
        self._update_arrow()

    def _on_toggle(self):
        self._content.setVisible(self._toggle_btn.isChecked())
        self._update_arrow()

    def _update_arrow(self):
        title = self._toggle_btn.text().lstrip()
        # Remove existing arrow prefix
        for prefix in ['> ', 'v ']:
            if title.startswith(prefix):
                title = title[2:]
        arrow = 'v' if self._toggle_btn.isChecked() else '>'
        self._toggle_btn.setText(f"  {arrow}  {title}")

    def content_layout(self):
        return self._content_layout


class CubeViControlWindow(QWidget):
    """
    Control window on the primary monitor.
    Handles drag-drop, mouse rotation, zoom, and shows a scaled preview.
    """

    def __init__(self, output_window, debug=True):
        super().__init__()

        self.debug = debug
        self.output_window = output_window
        self.renderer = None
        self.ctx = None

        # Load saved settings
        self._settings = load_settings(debug=debug)

        # Mouse tracking
        self.last_mouse_x = 0
        self.last_mouse_y = 0
        self.mouse_pressed = False
        self.right_mouse_pressed = False

        # Inertia state (smooth spin after release)
        self._velocity_yaw = 0.0
        self._velocity_pitch = 0.0
        self._pan_velocity_x = 0.0
        self._pan_velocity_y = 0.0
        self._friction = 0.88  # decay per frame (lower = stops faster)
        self._inertia_threshold = 0.05  # stop below this speed

        # Smooth zoom
        self._zoom_target = None  # will be set after renderer init
        self._zoom_speed = 0.12  # interpolation speed (0-1, higher = snappier)

        # Rendering state
        self.needs_render = True

        self._init_ui()
        self._init_renderer()
        self._apply_settings(self._settings)

        # Set initial zoom target from renderer
        if self.renderer:
            self._zoom_target = self.renderer.camera_distance

        # Render/physics timer
        self.render_timer = QTimer()
        self.render_timer.timeout.connect(self._tick)
        self.render_timer.start(16)  # ~60 FPS

        if self.debug:
            print(f"\n=== CubeVi STL Viewer Started ===")
            print(f"Controls: L-drag=rotate, R-drag=pan, scroll=zoom, F=toggle C1, R=reset, S=save, Esc=quit")

    def _init_ui(self):
        """Initialize the polished control window UI"""
        self.setWindowTitle("CubeVi C1 Viewer")
        self.setGeometry(100, 100, 420, 860)
        self.setMinimumWidth(360)

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Title bar ──
        title_bar = QFrame()
        title_bar.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-bottom: 1px solid #333;
                padding: 0;
            }
        """)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(14, 8, 14, 8)
        title_lbl = QLabel("CubeVi C1 Viewer")
        title_lbl.setStyleSheet("color: #e0e0e0; font-size: 14px; font-weight: 700; border: none;")
        title_layout.addWidget(title_lbl)
        title_layout.addStretch()

        # Open file button
        open_btn = QPushButton("Open File")
        open_btn.setToolTip("Open an STL or 3MF file")
        open_btn.clicked.connect(self._open_file_dialog)
        title_layout.addWidget(open_btn)

        root.addWidget(title_bar)

        # ── Preview / drop zone ──
        self.drop_zone = DropZone()
        self.drop_zone.fileDropped.connect(self._load_file)
        self.drop_zone.setMouseTracking(True)
        root.addWidget(self.drop_zone, 1)  # stretch=1 so preview fills space

        # ── Status bar (filename + info) ──
        self.status_bar = QLabel("")
        self.status_bar.setStyleSheet("""
            QLabel {
                background-color: #1a1a1a;
                border-top: 1px solid #2a2a2a;
                color: #666;
                font-size: 10px;
                padding: 3px 10px;
            }
        """)
        self.status_bar.setFixedHeight(22)
        root.addWidget(self.status_bar)

        # ── Controls panel ──
        controls_frame = QFrame()
        controls_frame.setStyleSheet("QFrame { background-color: #222; }")
        controls_layout = QVBoxLayout(controls_frame)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(0)

        # -- View Settings section --
        view_section = CollapsibleSection("View Settings", collapsed=False)
        vl = view_section.content_layout()

        # View count
        view_count_row = QHBoxLayout()
        self._add_label(view_count_row, "Views")
        self.view_count_combo = QComboBox()
        self.view_count_combo.addItems(["8 views", "16 views", "40 views"])
        self.view_count_combo.setCurrentIndex(1)
        self.view_count_combo.setToolTip("Fewer views = smoother, fewer row boundaries. More = finer parallax.")
        view_count_row.addWidget(self.view_count_combo, 1)
        vl.addLayout(view_count_row)

        # View cone
        self.view_cone_slider, self.view_cone_value_label = self._make_slider(20, 60, 40)
        self._add_slider_to(vl, "View cone", self.view_cone_slider, self.view_cone_value_label, "40\u00b0",
                            "Horizontal view cone. Narrower = softer parallax.")

        # Smoothing
        self.smoothing_slider, self.smoothing_value_label = self._make_slider(0, 100, 70)
        self._add_slider_to(vl, "Smoothing", self.smoothing_slider, self.smoothing_value_label, "70%",
                            "Blend between adjacent views. 100% = smoothest.")

        # Boundary smooth
        self.cubic_slider, self.cubic_value_label = self._make_slider(0, 100, 0)
        self._add_slider_to(vl, "Boundary", self.cubic_slider, self.cubic_value_label, "0%",
                            "4-view cubic interpolation at row boundaries.")

        # Gamma
        self.gamma_slider, self.gamma_value_label = self._make_slider(50, 200, 100)
        self._add_slider_to(vl, "Gamma", self.gamma_slider, self.gamma_value_label, "1.00",
                            "Output gamma correction.")

        controls_layout.addWidget(view_section)

        # -- Material & Appearance section --
        mat_section = CollapsibleSection("Material", collapsed=False)
        ml = mat_section.content_layout()

        # Color preset dropdown
        preset_row = QHBoxLayout()
        self._add_label(preset_row, "Color")
        self.color_combo = QComboBox()
        self.color_combo.addItems([
            "White",
            "Light Gray",
            "Charcoal",
            "Steel Blue",
            "Warm Sand",
            "Mint",
            "Coral",
            "Gold",
        ])
        self.color_combo.setToolTip("Material color preset")
        preset_row.addWidget(self.color_combo, 1)
        ml.addLayout(preset_row)

        # Material preset
        material_row = QHBoxLayout()
        self._add_label(material_row, "Material")
        self.material_combo = QComboBox()
        self.material_combo.addItems([
            "Matte Plastic",
            "Glossy Plastic",
            "Brushed Metal",
            "Chrome",
            "Clay",
        ])
        self.material_combo.setToolTip("Surface material preset")
        material_row.addWidget(self.material_combo, 1)
        ml.addLayout(material_row)

        # Roughness
        self.roughness_slider, self.roughness_value_label = self._make_slider(0, 100, 55)
        self._add_slider_to(ml, "Roughness", self.roughness_slider, self.roughness_value_label, "0.55",
                            "Surface roughness. 0 = mirror, 1 = matte.")

        # Rim light
        self.rim_slider, self.rim_value_label = self._make_slider(0, 100, 60)
        self._add_slider_to(ml, "Rim light", self.rim_slider, self.rim_value_label, "0.60",
                            "Edge glow intensity. Helps the 3D pop on lenticular.")

        # AO
        self.ao_slider, self.ao_value_label = self._make_slider(0, 100, 40)
        self._add_slider_to(ml, "AO", self.ao_slider, self.ao_value_label, "0.40",
                            "Ambient occlusion. Darkens crevices and undercuts.")

        # Environment reflection
        self.env_slider, self.env_value_label = self._make_slider(0, 100, 15)
        self._add_slider_to(ml, "Env reflect", self.env_slider, self.env_value_label, "0.15",
                            "Fake environment reflection. Adds life to metallic surfaces.")

        # Light intensity
        self.light_slider, self.light_value_label = self._make_slider(20, 250, 100)
        self._add_slider_to(ml, "Brightness", self.light_slider, self.light_value_label, "1.00",
                            "Master light brightness. Increase if model looks dark.")

        controls_layout.addWidget(mat_section)

        # -- Backdrop section --
        bg_section = CollapsibleSection("Backdrop", collapsed=False)
        bl = bg_section.content_layout()

        # Backdrop mode
        bg_mode_row = QHBoxLayout()
        self._add_label(bg_mode_row, "Style")
        self.bg_mode_combo = QComboBox()
        self.bg_mode_combo.addItems([
            "Gradient",
            "Spotlight",
            "Studio",
            "Three-tone",
        ])
        self.bg_mode_combo.setToolTip("Background style")
        bg_mode_row.addWidget(self.bg_mode_combo, 1)
        bl.addLayout(bg_mode_row)

        # Backdrop preset
        bg_preset_row = QHBoxLayout()
        self._add_label(bg_preset_row, "Preset")
        self.bg_preset_combo = QComboBox()
        self.bg_preset_combo.addItems([
            "Dark Studio",
            "Clean White",
            "Deep Ocean",
            "Warm Sunset",
            "Midnight",
            "Forest",
            "Concrete",
            "Neon",
        ])
        self.bg_preset_combo.setToolTip("Background color preset")
        bg_preset_row.addWidget(self.bg_preset_combo, 1)
        bl.addLayout(bg_preset_row)

        controls_layout.addWidget(bg_section)

        # -- Lenticular Calibration section (collapsed by default) --
        calib_section = CollapsibleSection("Lenticular Calibration", collapsed=True)
        cl = calib_section.content_layout()

        self.slope_slider, self.slope_value_label = self._make_slider(50, 150, 102)
        self._add_slider_to(cl, "Slope", self.slope_slider, self.slope_value_label, "0.102",
                            "Lenticular obliquity. Tune if view bands are misaligned.")

        self.interval_slider, self.interval_value_label = self._make_slider(150, 250, 196)
        self._add_slider_to(cl, "Interval", self.interval_slider, self.interval_value_label, "19.6",
                            "Lenticular line spacing in subpixels.")

        self.x0_slider, self.x0_value_label = self._make_slider(0, 250, 36)
        self._add_slider_to(cl, "X0 offset", self.x0_slider, self.x0_value_label, "3.6",
                            "Phase offset (deviation).")

        controls_layout.addWidget(calib_section)

        root.addWidget(controls_frame)

        self.setLayout(root)
        self.setMouseTracking(True)
        self.drop_zone.setMouseTracking(True)

    # ── UI helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _make_slider(lo, hi, default):
        slider = QSlider(Qt.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(default)
        value_label = QLabel("")
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value_label.setFixedWidth(44)
        value_label.setStyleSheet("color: #888; font-size: 11px; font-variant-numeric: tabular-nums;")
        return slider, value_label

    @staticmethod
    def _add_label(layout, text):
        lbl = QLabel(text)
        lbl.setFixedWidth(70)
        lbl.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(lbl)

    def _add_slider_to(self, layout, name, slider, value_label, initial_text, tooltip):
        value_label.setText(initial_text)
        row = QHBoxLayout()
        self._add_label(row, name)
        slider.setToolTip(tooltip)
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        layout.addLayout(row)

    # ── Renderer init ─────────────────────────────────────────────────────────

    def _init_renderer(self):
        """Initialize ModernGL renderer and connect sliders"""
        try:
            self.ctx = moderngl.create_standalone_context()

            if self.debug:
                print(f"OpenGL: {self.ctx.info.get('GL_RENDERER', 'Unknown')}")

            self.renderer = CubeViRenderer(self.ctx, debug=self.debug)

            # Sync calibration sliders from device config
            self.slope_slider.setValue(max(50, min(150, int(50 + (self.renderer.slope - 0.05) * 1000))))
            self.slope_value_label.setText(f"{self.renderer.slope:.3f}")
            self.interval_slider.setValue(max(150, min(250, int(self.renderer.interval * 10))))
            self.interval_value_label.setText(f"{self.renderer.interval:.1f}")
            self.x0_slider.setValue(max(0, min(250, int(self.renderer.x0 * 10))))
            self.x0_value_label.setText(f"{self.renderer.x0:.1f}")

            # Sync view cone from renderer default
            self.view_cone_slider.setValue(int(self.renderer.view_cone_degrees))
            self.view_cone_value_label.setText(f"{int(self.renderer.view_cone_degrees)}\u00b0")

            # ── Connect signals ──

            def on_smoothing(v):
                self.smoothing_value_label.setText(f"{v}%")
                self.renderer.set_view_blend(v / 100.0)
                self.needs_render = True
            self.smoothing_slider.valueChanged.connect(on_smoothing)
            self.renderer.set_view_blend(self.smoothing_slider.value() / 100.0)

            def on_view_count(index):
                n = [8, 16, 40][index]
                self.renderer.set_num_views(n)
                self.needs_render = True
            self.view_count_combo.currentIndexChanged.connect(on_view_count)
            self.view_count_combo.setCurrentIndex([8, 16, 40].index(self.renderer.num_views))

            def on_gamma(v):
                g = v / 100.0
                self.gamma_value_label.setText(f"{g:.2f}")
                self.renderer.set_gamma(g)
                self.needs_render = True
            self.gamma_slider.valueChanged.connect(on_gamma)
            self.renderer.set_gamma(self.gamma_slider.value() / 100.0)

            def on_cubic(v):
                self.cubic_value_label.setText(f"{v}%")
                self.renderer.set_cubic_blend(v / 100.0)
                self.needs_render = True
            self.cubic_slider.valueChanged.connect(on_cubic)
            self.renderer.set_cubic_blend(self.cubic_slider.value() / 100.0)

            def on_view_cone(v):
                self.view_cone_value_label.setText(f"{v}\u00b0")
                self.renderer.set_view_cone(v)
                self.needs_render = True
            self.view_cone_slider.valueChanged.connect(on_view_cone)
            self.renderer.set_view_cone(self.view_cone_slider.value())

            def on_slope(v):
                s = 0.05 + (v - 50) * 0.001
                self.slope_value_label.setText(f"{s:.3f}")
                self.renderer.set_calibration_slope(s)
                self.needs_render = True
            self.slope_slider.valueChanged.connect(on_slope)
            self.renderer.set_calibration_slope(0.05 + (self.slope_slider.value() - 50) * 0.001)

            def on_interval(v):
                i = v / 10.0
                self.interval_value_label.setText(f"{i:.1f}")
                self.renderer.set_calibration_interval(i)
                self.needs_render = True
            self.interval_slider.valueChanged.connect(on_interval)
            self.renderer.set_calibration_interval(self.interval_slider.value() / 10.0)

            def on_x0(v):
                x = v / 10.0
                self.x0_value_label.setText(f"{x:.1f}")
                self.renderer.set_calibration_x0(x)
                self.needs_render = True
            self.x0_slider.valueChanged.connect(on_x0)
            self.renderer.set_calibration_x0(self.x0_slider.value() / 10.0)

            # ── Material / Appearance signals ──

            COLOR_PRESETS = {
                "White":      (0.85, 0.85, 0.88),
                "Light Gray": (0.65, 0.65, 0.68),
                "Charcoal":   (0.25, 0.25, 0.28),
                "Steel Blue": (0.45, 0.55, 0.70),
                "Warm Sand":  (0.82, 0.72, 0.58),
                "Mint":       (0.55, 0.82, 0.72),
                "Coral":      (0.88, 0.52, 0.48),
                "Gold":       (0.85, 0.72, 0.35),
            }

            MATERIAL_PRESETS = {
                "Matte Plastic":  {"metallic": 0.0, "roughness": 0.65, "rim": 0.5},
                "Glossy Plastic": {"metallic": 0.0, "roughness": 0.25, "rim": 0.7},
                "Brushed Metal":  {"metallic": 0.85, "roughness": 0.45, "rim": 0.4},
                "Chrome":         {"metallic": 1.0, "roughness": 0.05, "rim": 0.8},
                "Clay":           {"metallic": 0.0, "roughness": 0.90, "rim": 0.3},
            }

            def on_color_preset(index):
                name = self.color_combo.currentText()
                if name in COLOR_PRESETS:
                    c = COLOR_PRESETS[name]
                    self.renderer.set_model_color(*c)
                    self.needs_render = True
            self.color_combo.currentIndexChanged.connect(on_color_preset)
            # Apply initial
            on_color_preset(0)

            def on_material_preset(index):
                name = self.material_combo.currentText()
                if name in MATERIAL_PRESETS:
                    p = MATERIAL_PRESETS[name]
                    self.renderer.set_metallic(p["metallic"])
                    self.renderer.set_roughness(p["roughness"])
                    self.renderer.set_rim_strength(p["rim"])
                    # Sync sliders (block signals to avoid double-update)
                    self.roughness_slider.blockSignals(True)
                    self.roughness_slider.setValue(int(p["roughness"] * 100))
                    self.roughness_value_label.setText(f"{p['roughness']:.2f}")
                    self.roughness_slider.blockSignals(False)
                    self.rim_slider.blockSignals(True)
                    self.rim_slider.setValue(int(p["rim"] * 100))
                    self.rim_value_label.setText(f"{p['rim']:.2f}")
                    self.rim_slider.blockSignals(False)
                    self.needs_render = True
            self.material_combo.currentIndexChanged.connect(on_material_preset)
            on_material_preset(0)

            def on_roughness(v):
                r = v / 100.0
                self.roughness_value_label.setText(f"{r:.2f}")
                self.renderer.set_roughness(r)
                self.needs_render = True
            self.roughness_slider.valueChanged.connect(on_roughness)

            def on_rim(v):
                r = v / 100.0
                self.rim_value_label.setText(f"{r:.2f}")
                self.renderer.set_rim_strength(r)
                self.needs_render = True
            self.rim_slider.valueChanged.connect(on_rim)

            def on_ao(v):
                a = v / 100.0
                self.ao_value_label.setText(f"{a:.2f}")
                self.renderer.set_ao_strength(a)
                self.needs_render = True
            self.ao_slider.valueChanged.connect(on_ao)
            self.renderer.set_ao_strength(self.ao_slider.value() / 100.0)

            def on_env(v):
                e = v / 100.0
                self.env_value_label.setText(f"{e:.2f}")
                self.renderer.set_env_reflect(e)
                self.needs_render = True
            self.env_slider.valueChanged.connect(on_env)
            self.renderer.set_env_reflect(self.env_slider.value() / 100.0)

            def on_light_intensity(v):
                i = v / 100.0
                self.light_value_label.setText(f"{i:.2f}")
                self.renderer.set_light_intensity(i)
                self.needs_render = True
            self.light_slider.valueChanged.connect(on_light_intensity)
            self.renderer.set_light_intensity(self.light_slider.value() / 100.0)

            # ── Backdrop signals ──

            BG_PRESETS = {
                "Dark Studio":  {"top": (0.18, 0.20, 0.24), "bottom": (0.06, 0.06, 0.08), "accent": (0.25, 0.25, 0.30)},
                "Clean White":  {"top": (0.92, 0.92, 0.94), "bottom": (0.75, 0.75, 0.78), "accent": (0.85, 0.85, 0.88)},
                "Deep Ocean":   {"top": (0.05, 0.08, 0.15), "bottom": (0.02, 0.03, 0.06), "accent": (0.08, 0.15, 0.30)},
                "Warm Sunset":  {"top": (0.35, 0.15, 0.10), "bottom": (0.08, 0.04, 0.03), "accent": (0.50, 0.25, 0.12)},
                "Midnight":     {"top": (0.08, 0.06, 0.14), "bottom": (0.02, 0.02, 0.04), "accent": (0.12, 0.10, 0.22)},
                "Forest":       {"top": (0.08, 0.14, 0.08), "bottom": (0.03, 0.05, 0.03), "accent": (0.12, 0.22, 0.12)},
                "Concrete":     {"top": (0.30, 0.30, 0.30), "bottom": (0.15, 0.15, 0.15), "accent": (0.22, 0.22, 0.22)},
                "Neon":         {"top": (0.06, 0.02, 0.12), "bottom": (0.02, 0.01, 0.04), "accent": (0.20, 0.05, 0.35)},
            }

            def on_bg_mode(index):
                self.renderer.set_bg_mode(index)
                self.needs_render = True
            self.bg_mode_combo.currentIndexChanged.connect(on_bg_mode)

            def on_bg_preset(index):
                name = self.bg_preset_combo.currentText()
                if name in BG_PRESETS:
                    p = BG_PRESETS[name]
                    self.renderer.set_bg_gradient(p["top"], p["bottom"])
                    self.renderer.set_bg_accent(*p["accent"])
                    self.needs_render = True
            self.bg_preset_combo.currentIndexChanged.connect(on_bg_preset)
            on_bg_preset(0)

        except Exception as e:
            print(f"Failed to initialize renderer: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    # ── Settings persistence ──────────────────────────────────────────────────

    def _apply_settings(self, s):
        """Apply loaded settings to UI widgets (triggers their signals -> renderer)."""
        # View settings
        view_map = {8: 0, 16: 1, 40: 2}
        self.view_count_combo.setCurrentIndex(view_map.get(s.get('num_views', 16), 1))
        self.view_cone_slider.setValue(int(s.get('view_cone_degrees', 40)))
        self.smoothing_slider.setValue(int(s.get('view_blend', 0.7) * 100))
        self.cubic_slider.setValue(int(s.get('cubic_blend', 0.0) * 100))
        self.gamma_slider.setValue(int(s.get('gamma', 1.0) * 100))

        # Material
        color_name = s.get('color_preset', 'White')
        idx = self.color_combo.findText(color_name)
        if idx >= 0:
            self.color_combo.setCurrentIndex(idx)

        mat_name = s.get('material_preset', 'Matte Plastic')
        idx = self.material_combo.findText(mat_name)
        if idx >= 0:
            self.material_combo.setCurrentIndex(idx)

        self.roughness_slider.setValue(int(s.get('roughness', 0.55) * 100))
        self.rim_slider.setValue(int(s.get('rim_strength', 0.6) * 100))
        self.ao_slider.setValue(int(s.get('ao_strength', 0.4) * 100))
        self.env_slider.setValue(int(s.get('env_reflect', 0.15) * 100))
        self.light_slider.setValue(int(s.get('light_intensity', 1.0) * 100))

        # Backdrop
        self.bg_mode_combo.setCurrentIndex(s.get('bg_mode', 0))
        bg_preset_name = s.get('bg_preset', 'Dark Studio')
        idx = self.bg_preset_combo.findText(bg_preset_name)
        if idx >= 0:
            self.bg_preset_combo.setCurrentIndex(idx)

        # Lenticular overrides (only if user has tweaked them)
        if s.get('slope') is not None:
            slope_val = max(50, min(150, int(50 + (s['slope'] - 0.05) * 1000)))
            self.slope_slider.setValue(slope_val)
        if s.get('interval') is not None:
            self.interval_slider.setValue(max(150, min(250, int(s['interval'] * 10))))
        if s.get('x0') is not None:
            self.x0_slider.setValue(max(0, min(250, int(s['x0'] * 10))))

        # Camera
        if self.renderer:
            dist = s.get('camera_distance', 1.77)
            self.renderer.set_camera_distance(dist)
            self._zoom_target = dist

        # Window geometry
        self.setGeometry(
            s.get('window_x', 100), s.get('window_y', 100),
            s.get('window_w', 420), s.get('window_h', 780)
        )

        self.needs_render = True

    def _gather_settings(self):
        """Collect current state into a settings dict for saving."""
        s = {}

        # View
        s['num_views'] = [8, 16, 40][self.view_count_combo.currentIndex()]
        s['view_cone_degrees'] = self.view_cone_slider.value()
        s['view_blend'] = self.smoothing_slider.value() / 100.0
        s['cubic_blend'] = self.cubic_slider.value() / 100.0
        s['gamma'] = self.gamma_slider.value() / 100.0

        # Material
        s['color_preset'] = self.color_combo.currentText()
        s['material_preset'] = self.material_combo.currentText()
        s['roughness'] = self.roughness_slider.value() / 100.0
        s['rim_strength'] = self.rim_slider.value() / 100.0
        s['ao_strength'] = self.ao_slider.value() / 100.0
        s['env_reflect'] = self.env_slider.value() / 100.0
        s['light_intensity'] = self.light_slider.value() / 100.0

        # Backdrop
        s['bg_mode'] = self.bg_mode_combo.currentIndex()
        s['bg_preset'] = self.bg_preset_combo.currentText()

        # Lenticular
        if self.renderer:
            s['slope'] = self.renderer.slope
            s['interval'] = self.renderer.interval
            s['x0'] = self.renderer.x0

        # Camera
        if self.renderer:
            s['camera_distance'] = self.renderer.camera_distance

        # Window geometry
        geo = self.geometry()
        s['window_x'] = geo.x()
        s['window_y'] = geo.y()
        s['window_w'] = geo.width()
        s['window_h'] = geo.height()

        return s

    def closeEvent(self, event):
        """Save settings on window close."""
        try:
            settings = self._gather_settings()
            save_settings(settings, debug=self.debug)
        except Exception as e:
            print(f"Warning: failed to save settings on exit: {e}")
        event.accept()

    # ── File loading ──────────────────────────────────────────────────────────

    def _open_file_dialog(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open 3D Model", "",
            "3D Models (*.stl *.3mf);;STL Files (*.stl);;3MF Files (*.3mf);;All Files (*)"
        )
        if filepath:
            self._load_file(filepath)

    def _load_file(self, filepath):
        if self.debug:
            print(f"\nLoading: {os.path.basename(filepath)}")

        success = self.renderer.load_stl(filepath)

        if success:
            self.drop_zone.show_loaded(os.path.basename(filepath))
            self.status_bar.setText(f"  {os.path.basename(filepath)}  |  "
                                    f"{self.renderer.vertex_count // 3:,} triangles")
            self.status_bar.setStyleSheet("""
                QLabel {
                    background-color: #1a1a1a;
                    border-top: 1px solid #2a2a2a;
                    color: #7ab8e0;
                    font-size: 10px;
                    padding: 3px 10px;
                }
            """)
            self.needs_render = True
        else:
            self.drop_zone.show_error(os.path.basename(filepath))
            self.status_bar.setText(f"  Failed to load {os.path.basename(filepath)}")
            self.status_bar.setStyleSheet("""
                QLabel {
                    background-color: #1a1a1a;
                    border-top: 1px solid #2a2a2a;
                    color: #d45a5a;
                    font-size: 10px;
                    padding: 3px 10px;
                }
            """)

    # ── Mouse interaction ───────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.mouse_pressed = True
            self.last_mouse_x = event.x()
            self.last_mouse_y = event.y()
            # Kill rotation inertia on click
            self._velocity_yaw = 0.0
            self._velocity_pitch = 0.0
        elif event.button() == Qt.RightButton:
            self.right_mouse_pressed = True
            self.last_mouse_x = event.x()
            self.last_mouse_y = event.y()
            # Kill pan inertia on click
            self._pan_velocity_x = 0.0
            self._pan_velocity_y = 0.0
        elif event.button() == Qt.MiddleButton:
            # Middle-click = instant reset (rotation + pan + zoom)
            if self.renderer and self.renderer.mesh_vao is not None:
                self.renderer.set_rotation(0, 0, 0)
                self.renderer.reset_pan()
                self.renderer.set_camera_distance(1.77)
                self._zoom_target = 1.77
                self._velocity_yaw = 0.0
                self._velocity_pitch = 0.0
                self._pan_velocity_x = 0.0
                self._pan_velocity_y = 0.0
                self.needs_render = True

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.mouse_pressed = False
        elif event.button() == Qt.RightButton:
            self.right_mouse_pressed = False

    def mouseMoveEvent(self, event):
        if self.renderer is None or self.renderer.mesh_vao is None:
            return

        dx = event.x() - self.last_mouse_x
        dy = event.y() - self.last_mouse_y
        precision = event.modifiers() & Qt.ShiftModifier

        if self.mouse_pressed:
            # Left drag = orbit (Fusion 360 convention)
            # Drag right -> camera orbits right (yaw increases)
            # Drag down -> camera orbits down (pitch decreases)
            speed = 0.1 if precision else 0.4
            dyaw = dx * speed
            dpitch = -dy * speed  # Negate: drag down = orbit down

            self.renderer.rotate(delta_pitch=dpitch, delta_yaw=dyaw)

            self._velocity_yaw = self._velocity_yaw * 0.3 + dyaw * 0.7
            self._velocity_pitch = self._velocity_pitch * 0.3 + dpitch * 0.7

            self.last_mouse_x = event.x()
            self.last_mouse_y = event.y()
            self.needs_render = True

        elif self.right_mouse_pressed:
            # Right drag = pan pivot in screen-space (respects orbit angle)
            yaw_rad = math.radians(self.renderer.model_rotation[1])
            pitch_rad = math.radians(self.renderer.model_rotation[0])

            base_speed = 0.0008 if precision else 0.003
            pan_speed = base_speed * self.renderer.camera_distance

            # Screen-space deltas (Fusion 360 convention: drag up = view up)
            sdx = -dx * pan_speed
            sdy = -dy * pan_speed  # Negate: drag up (negative dy) = pivot moves up

            # Convert to world-space using camera's local axes
            cos_y, sin_y = math.cos(yaw_rad), math.sin(yaw_rad)
            cos_p = math.cos(pitch_rad)
            sin_p = math.sin(pitch_rad)

            wx = sdx * cos_y + sdy * sin_p * sin_y
            wy = sdy * cos_p
            wz = -sdx * sin_y + sdy * sin_p * cos_y

            self.renderer.pan_3d(wx, wy, wz)

            self._pan_velocity_x = self._pan_velocity_x * 0.3 + sdx * 0.7
            self._pan_velocity_y = self._pan_velocity_y * 0.3 + sdy * 0.7

            self.last_mouse_x = event.x()
            self.last_mouse_y = event.y()
            self.needs_render = True

    def wheelEvent(self, event):
        if self.renderer and self.renderer.mesh_vao is not None:
            delta = event.angleDelta().y()
            # Shift = fine zoom (smaller steps)
            factor = 0.95 if event.modifiers() & Qt.ShiftModifier else 0.85
            if self._zoom_target is None:
                self._zoom_target = self.renderer.camera_distance
            if delta > 0:
                self._zoom_target *= factor  # zoom in
            else:
                self._zoom_target /= factor  # zoom out
            # Clamp
            self._zoom_target = max(1.0, min(10.0, self._zoom_target))

    # ── Tick: physics + render ────────────────────────────────────────────────

    def _tick(self):
        """Run inertia/zoom physics, then render if needed."""
        if self.renderer is None or self.renderer.mesh_vao is None:
            if self.needs_render:
                self._render_frame()
            return

        physics_dirty = False

        # Rotation inertia (already in orbit-space from mouseMoveEvent)
        if not self.mouse_pressed:
            if abs(self._velocity_yaw) > self._inertia_threshold or \
               abs(self._velocity_pitch) > self._inertia_threshold:
                self.renderer.rotate(
                    delta_pitch=self._velocity_pitch,
                    delta_yaw=self._velocity_yaw
                )
                self._velocity_yaw *= self._friction
                self._velocity_pitch *= self._friction
                physics_dirty = True
            else:
                self._velocity_yaw = 0.0
                self._velocity_pitch = 0.0

        # Pan inertia (screen-space velocity converted to world-space)
        if not self.right_mouse_pressed:
            pan_threshold = 0.0001
            if abs(self._pan_velocity_x) > pan_threshold or \
               abs(self._pan_velocity_y) > pan_threshold:
                yaw_rad = math.radians(self.renderer.model_rotation[1])
                pitch_rad = math.radians(self.renderer.model_rotation[0])
                cos_y, sin_y = math.cos(yaw_rad), math.sin(yaw_rad)
                cos_p, sin_p = math.cos(pitch_rad), math.sin(pitch_rad)
                sdx = self._pan_velocity_x
                sdy = self._pan_velocity_y
                wx = sdx * cos_y + sdy * sin_p * sin_y
                wy = sdy * cos_p
                wz = -sdx * sin_y + sdy * sin_p * cos_y
                self.renderer.pan_3d(wx, wy, wz)
                self._pan_velocity_x *= self._friction
                self._pan_velocity_y *= self._friction
                physics_dirty = True
            else:
                self._pan_velocity_x = 0.0
                self._pan_velocity_y = 0.0

        # Smooth zoom: lerp toward target
        if self._zoom_target is not None:
            current = self.renderer.camera_distance
            diff = self._zoom_target - current
            if abs(diff) > 0.002:
                new_dist = current + diff * self._zoom_speed
                self.renderer.set_camera_distance(new_dist)
                physics_dirty = True
            else:
                self.renderer.set_camera_distance(self._zoom_target)
                self._zoom_target = None

        if physics_dirty:
            self.needs_render = True

        if self.needs_render:
            self._render_frame()

    def _render_frame(self):
        """Render and update both windows."""
        if self.renderer is None or self.renderer.mesh_vao is None:
            return

        try:
            buf = self.renderer.render_quilt()

            if buf is not None:
                w = self.renderer.OUTPUT_WIDTH
                h = self.renderer.OUTPUT_HEIGHT
                bpl = w * 4

                # Wrap raw bytearray as QImage — zero-copy, no PIL
                qimage = QImage(buf, w, h, bpl, QImage.Format_RGBA8888)

                # Send pixel-perfect image to C1 output window
                self.output_window.set_image(qimage)

                # Show scaled preview in drop zone (FastTransformation = bilinear, much cheaper)
                preview_pixmap = QPixmap.fromImage(qimage).scaled(
                    self.drop_zone.size(),
                    Qt.KeepAspectRatio,
                    Qt.FastTransformation
                )
                self.drop_zone.setPixmap(preview_pixmap)

                self.needs_render = False

        except Exception as e:
            print(f"Render error: {e}")
            import traceback
            traceback.print_exc()

    # ── Keyboard shortcuts ────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()  # triggers closeEvent -> saves settings

        elif event.key() == Qt.Key_F:
            if self.output_window.isVisible():
                self.output_window.hide()
                if self.debug:
                    print("C1 output: hidden")
            else:
                c1_index, c1_screen = find_cubevi_screen(QApplication.instance())
                if c1_screen is not None:
                    self.output_window.place_on_screen(c1_screen)
                else:
                    self.output_window.show()
                self.needs_render = True
                if self.debug:
                    print("C1 output: visible")

        elif event.key() == Qt.Key_R:
            if self.renderer and self.renderer.mesh_vao is not None:
                self.renderer.set_rotation(0, 0, 0)
                self.renderer.reset_pan()
                self.renderer.set_camera_distance(1.77)
                self._zoom_target = 1.77
                self._velocity_yaw = 0.0
                self._velocity_pitch = 0.0
                self._pan_velocity_x = 0.0
                self._pan_velocity_y = 0.0
                self.needs_render = True
                if self.debug:
                    print("View reset")

        elif event.key() == Qt.Key_S:
            try:
                buf = self.renderer.render_quilt()
                if buf:
                    w = self.renderer.OUTPUT_WIDTH
                    h = self.renderer.OUTPUT_HEIGHT
                    img = QImage(buf, w, h, w * 4, QImage.Format_RGBA8888)
                    img.save("cubevi_output.png")
                    print(f"Saved cubevi_output.png")
            except Exception as e:
                print(f"Save failed: {e}")


def find_cubevi_screen(app):
    """
    Find the CubeVi C1 display among connected screens.
    C1 specs: 1440x2560 portrait, 5.7" (~127x226mm physical), 60Hz.
    """
    screens = app.screens()

    # Pass 1: exact portrait match (1440x2560)
    for i, screen in enumerate(screens):
        geo = screen.geometry()
        if geo.width() == 1440 and geo.height() == 2560:
            return i, screen

    # Pass 2: landscape 2560x1440 but only if it's a small display (<200mm wide)
    for i, screen in enumerate(screens):
        geo = screen.geometry()
        phys = screen.physicalSize()
        if geo.width() == 2560 and geo.height() == 1440 and phys.width() < 200:
            return i, screen

    # Pass 3: fallback — any non-primary small screen with 1440 in one dimension
    for i, screen in enumerate(screens):
        if screen == app.primaryScreen():
            continue
        geo = screen.geometry()
        if geo.width() == 1440 or geo.height() == 1440:
            phys = screen.physicalSize()
            if phys.width() < 200 or phys.height() < 200:
                return i, screen

    return None, None


def main():
    # Disable DPI scaling to ensure 1:1 pixel mapping on C1
    os.environ['QT_AUTO_SCREEN_SCALE_FACTOR'] = '0'
    os.environ['QT_SCALE_FACTOR'] = '1'
    QApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)

    # Set up file logging — all print() output goes to cubevi_viewer.log
    logger = setup_logging()
    redirect_stdio(logger)

    DEBUG = True

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)

    # Create output window (will be shown on C1)
    output_window = CubeViOutputWindow()

    # Create control window (stays on primary monitor)
    control_window = CubeViControlWindow(output_window, debug=DEBUG)
    control_window.show()

    # Find and position on CubeVi C1 display
    c1_index, c1_screen = find_cubevi_screen(app)

    if c1_screen is not None:
        output_window.place_on_screen(c1_screen)

        if DEBUG:
            geo = c1_screen.geometry()
            screens = app.screens()
            print(f"\n=== Display Setup ===")
            for i, s in enumerate(screens):
                g = s.geometry()
                marker = " <-- CubeVi C1" if i == c1_index else ""
                print(f"  Display {i} ({s.name()}): {g.width()}x{g.height()} at ({g.x()},{g.y()}){marker}")
            print(f"  Output window: {geo.width()}x{geo.height()} at ({geo.x()},{geo.y()})")
    else:
        output_window.setGeometry(600, 100, 720, 1280)
        output_window.show()

        if DEBUG:
            print(f"\n=== Display Setup ===")
            print(f"  CubeVi C1 not detected — output in windowed mode")
            print(f"  Press F to toggle fullscreen")

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
