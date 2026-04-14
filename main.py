import sys
import math
import json
import os
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                               QLabel, QSlider, QPushButton, QComboBox, QColorDialog, QCheckBox)
from PySide6.QtCore import Qt, QTimer, QRectF, QSettings
from PySide6.QtGui import QPainter, QColor, QPen

# Determine the absolute path for the config file in the same directory as the script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

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
            self.dash_offset -= (self.speed / 10.0)
            self.update()
        elif self.pattern in ["Breathing", "Rainbow", "Strobe"]:
            self.phase += (self.speed / 50.0)
            self.update()
        elif self.pattern == "Solid":
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
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
            
        glow_passes = 6
        for i in range(glow_passes):
            glow_factor = (i + 1) / glow_passes
            current_thickness = self.thickness * (1.0 + (glow_passes - 1 - i) * 0.5)
            
            if i == glow_passes - 1:
                alpha = eff_brightness
            else:
                alpha = eff_brightness * 0.2 * glow_factor
                
            c = QColor(draw_color)
            c.setAlphaF(max(0.0, min(1.0, alpha)))
            
            pen = QPen(c)
            pen.setWidthF(current_thickness)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            
            inset = current_thickness / 2.0
            draw_rect = QRectF(rect).adjusted(inset, inset, -inset, -inset)

            if self.pattern == "Chase":
                dash_len = self.led_length
                # dash pattern: [line_length, space_length]
                pen.setDashPattern([dash_len / 5.0, dash_len / 5.0])
                pen.setDashOffset(self.dash_offset)
            else:
                pen.setStyle(Qt.PenStyle.SolidLine)
                
            painter.setPen(pen)
            painter.drawRect(draw_rect)

class ControlPanel(QWidget):
    def __init__(self, overlays):
        super().__init__()
        self.overlays = overlays
        self.setWindowTitle("Monitor LED Config")
        
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
