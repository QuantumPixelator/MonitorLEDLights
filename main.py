import sys
import math
import json
import os
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                               QLabel, QSlider, QPushButton, QComboBox, QColorDialog, QCheckBox)
from PySide6.QtCore import Qt, QTimer, QRectF, QSettings
from PySide6.QtGui import QPainter, QColor, QPen, QIcon, QImage

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
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16) # ~60fps

        # Offscreen frame; avoids stale pixels on translucent layered windows (Windows DWM).
        self._frame_buffer = None

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
        self.update()
        
    def update_animation(self):
        if self.pattern == "Chase":
            dash_on = max(1.0, self.led_length / 6.0)
            dash_off = max(1.0, dash_on * 1.5)
            pattern_span = dash_on + dash_off
            self.dash_offset = (self.dash_offset - (self.speed / 10.0)) % pattern_span
            self.update(self.rect())
        elif self.pattern in ["Breathing", "Rainbow", "Strobe"]:
            self.phase += (self.speed / 50.0)
            self.update(self.rect())
        elif self.pattern == "Solid":
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

        # Chase: fewer halo passes so motion does not read as smeared "echoes".
        glow_passes = 3 if self.pattern == "Chase" else 5
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

            if self.pattern == "Chase":
                dash_len = self.led_length
                pen.setDashPattern([max(1.0, dash_len / 6.0), max(1.0, dash_len / 4.0)])
                pen.setDashOffset(self.dash_offset)
            else:
                pen.setStyle(Qt.PenStyle.SolidLine)

            buf.setPen(pen)
            buf.drawRect(draw_rect)

        buf.end()

        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(0, 0, self._frame_buffer)

class ControlPanel(QWidget):
    def __init__(self, overlays):
        super().__init__()
        self.overlays = overlays
        self.setWindowTitle("Monitor LED Config")
        self.setWindowIcon(QApplication.windowIcon())
        
        self.settings = self.load_config()
        self.init_ui()
        self.notify_overlays()
        
    def load_config(self):
        defaults = {
            "color": "#00ffff",
            "brightness": 100,
            "pattern": "Chase",
            "speed": 50,
            "thickness": 15,
            "led_length": 100,
            "startup": False
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
        # Use simple absolute path for the entry point
        app_path = os.path.abspath(sys.argv[0])
        py_exe = sys.executable
        
        run_key = QSettings("HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", QSettings.Format.NativeFormat)
        
        if enabled:
            # Command to run: "python.exe" "path_to_script.py"
            run_key.setValue(app_name, f"\"{py_exe}\" \"{app_path}\"")
        else:
            run_key.remove(app_name)

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)
        
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
        self.combo_pattern.addItems(["Solid", "Chase", "Breathing", "Rainbow", "Strobe"])
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
        btn_exit.clicked.connect(self.close)
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

    def toggle_startup(self, state):
        self.settings["startup"] = (state == 2)
        self.save_config()
        
    def notify_overlays(self):
        for overlay in self.overlays:
            overlay.update_settings(self.settings)
            
    def closeEvent(self, event):
        QApplication.quit()
        event.accept()

def main():
    app = QApplication(sys.argv)
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
        
    panel = ControlPanel(overlays)
    panel.show()
    
    app.setQuitOnLastWindowClosed(True)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
