import sys
import math
import json
import os
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QSlider, QPushButton, QComboBox, QColorDialog, QCheckBox,
                               QSystemTrayIcon, QMenu, QDialog, QMessageBox)
from PySide6.QtCore import Qt, QTimer, QRectF, QSettings, QPointF, QCoreApplication, QElapsedTimer
from PySide6.QtGui import QAction, QPainter, QColor, QPen, QIcon, QImage

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass

# Determine the absolute path for the config file in the same directory as the script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
ICON_FILE = os.path.join(SCRIPT_DIR, "assets", "monitorlights.ico")

class LEDOverlay(QWidget):
    def __init__(self, screen_geometry):
        super().__init__()
        # Always on top, click-through, frameless
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setGeometry(screen_geometry)
        
        self.base_color = QColor(0, 255, 255)
        self.brightness = 1.0
        self.pattern = "Chase"
        self.speed = 50
        self.thickness = 15
        self.led_length = 100.0
        
        self.dash_offset = 0.0
        self.phase = 0.0
        self._enabled = True

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)  # ~60fps

        self._chase_elapsed = QElapsedTimer()
        self._chase_elapsed.start()

        # Offscreen frame; avoids stale pixels on translucent layered windows (Windows DWM).
        self._frame_buffer = None

    @staticmethod
    def _iter_rect_perimeter_segments(draw_rect, step):
        """Yield (x1, y1, x2, y2, s_mid) for axis-aligned rect; s_mid is arc length to segment center from top-left clockwise."""
        L, T, R, B = draw_rect.left(), draw_rect.top(), draw_rect.right(), draw_rect.bottom()
        if draw_rect.width() <= 0 or draw_rect.height() <= 0:
            return
        s = 0.0
        x = L
        while x < R - 1e-9:
            seg = min(step, R - x)
            mid = s + seg * 0.5
            yield (x, T, x + seg, T, mid)
            s += seg
            x += seg
        y = T
        while y < B - 1e-9:
            seg = min(step, B - y)
            mid = s + seg * 0.5
            yield (R, y, R, y + seg, mid)
            s += seg
            y += seg
        x = R
        while x > L + 1e-9:
            seg = min(step, x - L)
            mid = s + seg * 0.5
            yield (x, B, x - seg, B, mid)
            s += seg
            x -= seg
        y = B
        while y > T + 1e-9:
            seg = min(step, y - T)
            mid = s + seg * 0.5
            yield (L, y, L, y - seg, mid)
            s += seg
            y -= seg

    def _paint_rainbow_chase_border(self, buf, full_w, full_h, eff_brightness):
        """Chase pattern: dashed border with rainbow hues along the perimeter (concentric glow passes)."""
        dash_on = max(1.0, self.led_length / 6.0)
        dash_off = max(1.0, dash_on * 1.5)
        pattern_span = dash_on + dash_off
        step = max(2.0, self.thickness * 0.45)
        glow_passes = 3

        for i in range(glow_passes):
            glow_factor = (i + 1) / glow_passes
            current_thickness = self.thickness * (1.0 + (glow_passes - 1 - i) * 0.38)
            inset = current_thickness / 2.0
            draw_rect = QRectF(0, 0, full_w, full_h).adjusted(inset, inset, -inset, -inset)
            perimeter = max(1.0, 2.0 * (draw_rect.width() + draw_rect.height()))

            for x1, y1, x2, y2, s_mid in self._iter_rect_perimeter_segments(draw_rect, step):
                pos = (s_mid + self.dash_offset) % pattern_span
                if pos >= dash_on:
                    continue
                hue = ((s_mid / perimeter) * 360.0 + self.dash_offset * 2.5) % 360.0
                c = QColor.fromHsvF(hue / 360.0, 0.85, 1.0)
                if i == glow_passes - 1:
                    alpha = eff_brightness
                else:
                    alpha = eff_brightness * 0.14 * glow_factor
                c.setAlphaF(max(0.0, min(1.0, alpha)))
                pen = QPen(c)
                pen.setWidthF(current_thickness)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setStyle(Qt.PenStyle.SolidLine)
                buf.setPen(pen)
                buf.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _paint_chase_border_solid(self, buf, full_w, full_h, eff_brightness, draw_color):
        """Chase using the selected LED color only (dashed rect, same motion as rainbow chase)."""
        glow_passes = 3
        for i in range(glow_passes):
            glow_factor = (i + 1) / glow_passes
            current_thickness = self.thickness * (1.0 + (glow_passes - 1 - i) * 0.38)
            if i == glow_passes - 1:
                alpha = eff_brightness
            else:
                alpha = eff_brightness * 0.14 * glow_factor
            c = QColor(draw_color)
            c.setAlphaF(max(0.0, min(1.0, alpha)))
            pen = QPen(c)
            pen.setWidthF(current_thickness)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            inset = current_thickness / 2.0
            draw_rect = QRectF(0, 0, full_w, full_h).adjusted(inset, inset, -inset, -inset)
            dash_len = self.led_length
            pen.setDashPattern([max(1.0, dash_len / 6.0), max(1.0, dash_len / 4.0)])
            pen.setDashOffset(self.dash_offset)
            buf.setPen(pen)
            buf.drawRect(draw_rect)

    def resizeEvent(self, event):
        self._frame_buffer = None
        super().resizeEvent(event)

    def update_settings(self, settings_dict):
        self.base_color = QColor(settings_dict.get("color", "#00ffff"))
        self.brightness = settings_dict.get("brightness", 100) / 100.0
        self.pattern = settings_dict.get("pattern", "Chase")
        self.speed = settings_dict.get("speed", 50)
        self.thickness = settings_dict.get("thickness", 15)
        self.led_length = settings_dict.get("led_length", 100)
        enabled = settings_dict.get("enabled", True)
        self._enabled = enabled
        self.setVisible(enabled)
        if enabled:
            if not self.timer.isActive():
                self.timer.start(16)
        else:
            self.timer.stop()
        self.update()

    def update_animation(self):
        if not self._enabled:
            return
        if self.pattern in ("Chase", "Chase Rainbow"):
            # Time-based so motion matches the speed slider even when paint is slow (Chase Rainbow).
            dt_ms = self._chase_elapsed.restart()
            if dt_ms < 1:
                dt_ms = 16
            dt_ms = min(dt_ms, 250)
            dt_s = dt_ms / 1000.0
            dash_on = max(1.0, self.led_length / 6.0)
            dash_off = max(1.0, dash_on * 1.5)
            pattern_span = dash_on + dash_off
            # px/s along stroke at speed 100 (slider 1–100 scales linearly)
            chase_px_per_sec = (self.speed / 100.0) * 1800.0
            self.dash_offset = (self.dash_offset - chase_px_per_sec * dt_s) % pattern_span
            self.update(self.rect())
        elif self.pattern in ["Breathing", "Rainbow", "Strobe"]:
            self._chase_elapsed.restart()
            self.phase += (self.speed / 50.0)
            self.update(self.rect())
        elif self.pattern == "Solid":
            self._chase_elapsed.restart()
            self.update(self.rect())

    def paintEvent(self, event):
        rect = self.rect()
        w, h = rect.width(), rect.height()
        if w <= 0 or h <= 0:
            return

        if self._frame_buffer is None or self._frame_buffer.size() != rect.size():
            self._frame_buffer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)

        self._frame_buffer.fill(Qt.GlobalColor.transparent)

        buf = QPainter(self._frame_buffer)
        buf.setRenderHint(QPainter.RenderHint.Antialiasing)

        eff_brightness = self.brightness
        draw_color = QColor(self.base_color)

        # Apply pattern logic
        if self.pattern == "Breathing":
            wave = (math.sin(self.phase * 0.1) + 1.0) / 2.0
            eff_brightness *= (0.2 + 0.8 * wave)
        elif self.pattern == "Rainbow":
            hue = (int(self.phase * 2) % 360) / 360.0
            draw_color = QColor.fromHsvF(hue, 0.8, 1.0)
        elif self.pattern == "Strobe":
            if (int(self.phase * 0.2) % 2) == 0:
                eff_brightness = 0.0

        if self.pattern == "Chase Rainbow":
            self._paint_rainbow_chase_border(buf, w, h, eff_brightness)
            buf.end()
        elif self.pattern == "Chase":
            self._paint_chase_border_solid(buf, w, h, eff_brightness, draw_color)
            buf.end()
        else:
            glow_passes = 5
            for i in range(glow_passes):
                glow_factor = (i + 1) / glow_passes
                current_thickness = self.thickness * (1.0 + (glow_passes - 1 - i) * 0.38)

                if i == glow_passes - 1:
                    alpha = eff_brightness
                else:
                    alpha = eff_brightness * 0.14 * glow_factor

                c = QColor(draw_color)
                c.setAlphaF(max(0.0, min(1.0, alpha)))

                pen = QPen(c)
                pen.setWidthF(current_thickness)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)

                inset = current_thickness / 2.0
                draw_rect = QRectF(0, 0, w, h).adjusted(inset, inset, -inset, -inset)

                pen.setStyle(Qt.PenStyle.SolidLine)

                buf.setPen(pen)
                buf.drawRect(draw_rect)

            buf.end()

        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(0, 0, self._frame_buffer)

def _windows_quiet_launcher_exe():
    """Prefer pythonw.exe so startup / Run key does not open a console window."""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[:-10] + "pythonw.exe"
        if os.path.isfile(cand):
            return cand
    return exe


class SettingsDialog(QDialog):
    def __init__(self, overlays, hide_on_close=True):
        super().__init__()
        self.overlays = overlays
        self._hide_on_close = hide_on_close
        self.setWindowTitle("Monitor LED Config")
        self.setWindowIcon(QApplication.windowIcon())
        self._tray_enabled_action = None

        self.settings = self.load_config()
        self.init_ui()
        self.notify_overlays()

    def set_tray_enabled_action(self, action):
        self._tray_enabled_action = action

    def load_config(self):
        defaults = {
            "color": "#00ffff",
            "brightness": 100,
            "pattern": "Chase",
            "speed": 50,
            "thickness": 15,
            "led_length": 100,
            "startup": False,
            "enabled": True,
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    defaults.update(data)
            except Exception:
                pass
        return defaults

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.settings, f)
            self.handle_startup_reg(self.settings.get("startup", False))
        except Exception as e:
            print(f"Error saving config: {e}")

    def handle_startup_reg(self, enabled):
        if sys.platform != "win32":
            return

        app_name = "Monitor LED Overlay"
        app_path = os.path.abspath(sys.argv[0])
        py_exe = _windows_quiet_launcher_exe()

        run_key = QSettings("HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", QSettings.Format.NativeFormat)

        if enabled:
            run_key.setValue(app_name, f"\"{py_exe}\" \"{app_path}\"")
        else:
            run_key.remove(app_name)

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)

        self.chk_enabled = QCheckBox("Overlay enabled")
        self.chk_enabled.setChecked(self.settings.get("enabled", True))
        self.chk_enabled.toggled.connect(self.on_enabled_toggled)
        layout.addWidget(self.chk_enabled)

        # Color Selector
        color_layout = QHBoxLayout()
        self.btn_color = QPushButton("Pick Color")
        self.btn_color.clicked.connect(self.choose_color)
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(24, 24)
        color_layout.addWidget(QLabel("LED Color:"))
        color_layout.addWidget(self.btn_color)
        color_layout.addWidget(self.color_preview)
        color_layout.addStretch()
        layout.addLayout(color_layout)
        self.update_color_preview()
        
        # Pattern Selector
        pattern_layout = QHBoxLayout()
        self.combo_pattern = QComboBox()
        self.combo_pattern.addItems(
            ["Solid", "Chase", "Chase Rainbow", "Breathing", "Rainbow", "Strobe"]
        )
        self.combo_pattern.setCurrentText(self.settings["pattern"])
        self.combo_pattern.currentTextChanged.connect(self.change_pattern)
        pattern_layout.addWidget(QLabel("Light Pattern:"))
        pattern_layout.addWidget(self.combo_pattern)
        layout.addLayout(pattern_layout)
        
        # Brightness Slider
        layout.addWidget(QLabel("Brightness:"))
        self.slider_brightness = QSlider(Qt.Orientation.Horizontal)
        self.slider_brightness.setRange(10, 100)
        self.slider_brightness.setValue(self.settings["brightness"])
        self.slider_brightness.valueChanged.connect(lambda v: self.update_setting("brightness", v))
        layout.addWidget(self.slider_brightness)
        
        # Speed Slider
        layout.addWidget(QLabel("Pattern Speed:"))
        self.slider_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_speed.setRange(1, 100)
        self.slider_speed.setValue(self.settings["speed"])
        self.slider_speed.valueChanged.connect(lambda v: self.update_setting("speed", v))
        layout.addWidget(self.slider_speed)

        # Thickness Slider
        layout.addWidget(QLabel("Strip Thickness:"))
        self.slider_thickness = QSlider(Qt.Orientation.Horizontal)
        self.slider_thickness.setRange(5, 40)
        self.slider_thickness.setValue(self.settings["thickness"])
        self.slider_thickness.valueChanged.connect(lambda v: self.update_setting("thickness", v))
        layout.addWidget(self.slider_thickness)

        # LED Length Slider (for Chase)
        layout.addWidget(QLabel("Chase Segment Length:"))
        self.slider_length = QSlider(Qt.Orientation.Horizontal)
        self.slider_length.setRange(20, 1000)
        self.slider_length.setValue(self.settings["led_length"])
        self.slider_length.valueChanged.connect(lambda v: self.update_setting("led_length", v))
        layout.addWidget(self.slider_length)

        # Startup Checkbox
        self.check_startup = QCheckBox("Run on Startup")
        self.check_startup.setChecked(self.settings["startup"])
        self.check_startup.stateChanged.connect(self.toggle_startup)
        layout.addWidget(self.check_startup)

        # Exit Button
        layout.addStretch()
        btn_exit = QPushButton("Exit App")
        btn_exit.clicked.connect(QCoreApplication.quit)
        layout.addWidget(btn_exit)
        
        self.setLayout(layout)
        self.resize(300, 480)
        self.setFixedSize(self.sizeHint())
        
    def update_color_preview(self):
        self.color_preview.setStyleSheet(f"background-color: {self.settings['color']}; border: 1px solid #999;")
        
    def choose_color(self):
        current_color = QColor(self.settings["color"])
        color = QColorDialog.getColor(current_color, self, "Choose LED Color")
        if color.isValid():
            self.settings["color"] = color.name()
            self.update_color_preview()
            self.notify_overlays()
            self.save_config()
            
    def change_pattern(self, pattern):
        self.settings["pattern"] = pattern
        self.notify_overlays()
        self.save_config()

    def update_setting(self, key, value):
        self.settings[key] = value
        self.notify_overlays()
        self.save_config()

    def on_enabled_toggled(self, checked: bool):
        self.settings["enabled"] = checked
        self.notify_overlays()
        self.save_config()
        if self._tray_enabled_action is not None:
            self._tray_enabled_action.blockSignals(True)
            self._tray_enabled_action.setChecked(checked)
            self._tray_enabled_action.blockSignals(False)

    def set_enabled_from_tray(self, checked: bool):
        self.chk_enabled.blockSignals(True)
        self.chk_enabled.setChecked(checked)
        self.chk_enabled.blockSignals(False)
        self.settings["enabled"] = checked
        self.notify_overlays()
        self.save_config()

    def show_settings(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_startup(self, state):
        self.settings["startup"] = (state == 2)
        self.save_config()
        
    def notify_overlays(self):
        for overlay in self.overlays:
            overlay.update_settings(self.settings)
            
    def closeEvent(self, event):
        if self._hide_on_close:
            event.ignore()
            self.hide()
        else:
            event.accept()
            QCoreApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Monitor LED Overlay")
    if os.path.exists(ICON_FILE):
        app.setWindowIcon(QIcon(ICON_FILE))

    overlays = []
    screens = app.screens()
    for screen in screens:
        geom = screen.geometry()
        overlay = LEDOverlay(geom)
        overlay.show()
        overlays.append(overlay)

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    settings = SettingsDialog(overlays, hide_on_close=tray_available)
    if tray_available:
        tray = QSystemTrayIcon(app)
        tray.setIcon(QIcon(ICON_FILE) if os.path.exists(ICON_FILE) else app.windowIcon())
        tray.setToolTip("Monitor LED Overlay")

        menu = QMenu()
        act_settings = QAction("Settings", menu)
        act_settings.triggered.connect(settings.show_settings)
        act_enabled = QAction("Overlay enabled", menu)
        act_enabled.setCheckable(True)
        act_enabled.setChecked(settings.settings.get("enabled", True))
        act_enabled.toggled.connect(settings.set_enabled_from_tray)
        settings.set_tray_enabled_action(act_enabled)
        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(QCoreApplication.quit)

        menu.addAction(act_settings)
        menu.addAction(act_enabled)
        menu.addSeparator()
        menu.addAction(act_quit)
        tray.setContextMenu(menu)

        def on_tray_activated(reason):
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
                settings.show_settings()

        tray.activated.connect(on_tray_activated)
        tray.show()
    else:
        QMessageBox.warning(
            settings,
            "Monitor LED",
            "System tray is not available. The settings window will stay open.",
        )
        settings.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
