import sys
import json
import threading
from typing import Dict

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QPushButton,
    QLabel,
    QSizePolicy,
)
from PySide6.QtCore import QTimer, Signal, QObject, Qt, QRectF
from PySide6.QtGui import QFont, QColor, QPainter, QPen

import paho.mqtt.client as mqtt


class MQTTWorker(QObject):
    """Worker class to handle MQTT communication in a separate thread"""
    data_received = Signal(dict)

    def __init__(self, broker_host="localhost", broker_port=1883):
        super().__init__()
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client = None
        self.is_running = False

    def setup_mqtt(self):
        """Setup MQTT client"""
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

    def on_connect(self, client, userdata, flags, rc, properties):
        """Callback for when MQTT client connects"""
        if rc == 0:
            print("Connected to MQTT broker")
            client.subscribe("virtualtrainer/mvcp")
            client.subscribe("virtualtrainer/data")
        else:
            print(f"Failed to connect to MQTT broker: {rc}")

    def on_message(self, client, userdata, msg):
        """Callback for when a message is received"""
        try:
            payload = msg.payload.decode('utf-8')
            data = json.loads(payload)
            self.data_received.emit(data)
        except json.JSONDecodeError:
            print(f"Invalid JSON received: {payload}")
        except Exception as e:
            print(f"Error processing message: {e}")

    def on_disconnect(self, client, userdata, flags, rc, properties):
        """Callback for when MQTT client disconnects"""
        print("Disconnected from MQTT broker")

    def start_mqtt(self):
        """Start MQTT client"""
        self.setup_mqtt()
        try:
            self.client.connect(self.broker_host, self.broker_port, 60)
            self.client.loop_start()
            self.is_running = True
            print(f"MQTT client started - connecting to {self.broker_host}:{self.broker_port}")
        except Exception as e:
            print(f"Failed to start MQTT client: {e}")

    def stop_mqtt(self):
        """Stop MQTT client"""
        if self.client and self.is_running:
            self.client.loop_stop()
            self.client.disconnect()
            self.is_running = False

    def send_command(self, command_data):
        """Send command to MQTT topic"""
        if self.client and self.is_running:
            try:
                command_json = json.dumps(command_data)
                self.client.publish("virtualtrainer/control", command_json)
                print(f"Sent command: {command_json}")
            except Exception as e:
                print(f"Failed to send command: {e}")


class BarChartWidget(QWidget):
    """
    Vlastní widget pro vykreslení sloupců:
    - plynulá animace mezi starou a novou hodnotou
    - barvy podle prahů (≤50 zelená, >50 oranžová, >80 červená)
    - Phase vždy modrá
    """

    def __init__(self, categories, phase_key="Phase", parent=None):
        super().__init__(parent)
        self.categories = list(categories)
        self.phase_key = phase_key

        # cílové a aktuálně zobrazené hodnoty
        self.values: Dict[str, float] = {}
        self.display_values: Dict[str, float] = {}

        # animační timer (cca 30 FPS)
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update_animation)
        self.anim_timer.start(30)

        # aby se widget roztahoval na maximum
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, categories, values: Dict[str, float]):
        """
        Nastaví nové kategorie a cílové hodnoty.
        :param categories: pořadí sloupců (seznam názvů svalů + případně Phase)
        :param values: dict {name: value}
        """
        self.categories = list(categories)

        # odeber staré klíče, které už nejsou ve values
        for k in list(self.values.keys()):
            if k not in values:
                del self.values[k]
        for k in list(self.display_values.keys()):
            if k not in values:
                del self.display_values[k]

        # nastav nové cíle
        for k, v in values.items():
            try:
                v_float = float(v)
            except Exception:
                v_float = 0.0
            v_float = max(0.0, min(100.0, v_float))  # clamp 0–100
            self.values[k] = v_float
            # při prvním výskytu nastavíme display = aktuální hodnota, ať to neskáče z nuly
            if k not in self.display_values:
                self.display_values[k] = v_float

        self.update()

    def update_animation(self):
        """Posun display_values směrem k values pro plynulou animaci."""
        if not self.values:
            return

        anything_changed = False
        # jednoduchý „easing“: vždy o 15 % k cíli
        alpha = 0.15

        for k, target in self.values.items():
            current = self.display_values.get(k, 0.0)
            diff = target - current
            if abs(diff) < 0.1:
                new_val = target
            else:
                new_val = current + diff * alpha

            if new_val != current:
                anything_changed = True
            self.display_values[k] = new_val

        if anything_changed:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()
        painter.fillRect(rect, self.palette().window())

        if not self.categories:
            return

        # rozložení – menší okraje, aby graf víc využil výšku
        left_margin = 50
        right_margin = 20
        top_margin = 10
        bottom_margin = 28

        chart_rect = QRectF(
            left_margin,
            top_margin,
            rect.width() - left_margin - right_margin,
            rect.height() - top_margin - bottom_margin,
        )

        # horizontální grid + popisky osy Y (0, 25, 50, 75, 100)
        painter.setFont(QFont("Arial", 8))
        for perc in [0, 25, 50, 75, 100]:
            y = chart_rect.bottom() - chart_rect.height() * perc / 100.0
            # grid line – tmavší
            painter.setPen(QPen(QColor(180, 180, 180)))
            painter.drawLine(chart_rect.left(), y, chart_rect.right(), y)
            # label vlevo
            painter.setPen(QPen(QColor(80, 80, 80)))
            painter.drawText(
                0,
                int(y - 6),
                left_margin - 5,
                12,
                Qt.AlignRight | Qt.AlignVCenter,
                str(perc),
            )

        # sloupce
        n = len(self.categories)
        if n == 0:
            return

        slot_width = chart_rect.width() / n
        # širší sloupce (cca 2× oproti původním)
        bar_width = min(80.0, slot_width * 0.8)

        for idx, cat in enumerate(self.categories):
            val = self.display_values.get(cat, 0.0)
            val = max(0.0, min(100.0, val))

            x_center = chart_rect.left() + slot_width * (idx + 0.5)
            bar_left = x_center - bar_width / 2
            y_bottom = chart_rect.bottom()
            bar_height = chart_rect.height() * val / 100.0
            y_top = y_bottom - bar_height

            # barva podle prahů, Phase vždy modrá
            if cat == self.phase_key:
                color = QColor("#2196F3")
            else:
                if val > 80.0:
                    color = QColor("#F44336")  # red
                elif val > 50.0:
                    color = QColor("#FF9800")  # orange
                else:
                    color = QColor("#4CAF50")  # green

            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(
                QRectF(bar_left, y_top, bar_width, bar_height),
                3,
                3,
            )

            # název svalu pod sloupcem
            painter.setPen(QPen(QColor(50, 50, 50)))
            painter.setFont(QFont("Arial", 9))
            label_rect = QRectF(
                bar_left - 10,
                chart_rect.bottom() + 2,
                bar_width + 20,
                bottom_margin - 2,
            )
            painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop, cat)

        painter.end()


class MVCPVisualizer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MVCP Real-time Visualizer")
        self.setGeometry(100, 100, 900, 700)

        # Muscle groups from your MQTT data
        self.muscle_groups = ["Biceps", "Triceps", "Quadriceps", "Gastrocnemius"]

        # Current MVCP values
        self.mvcp_data: Dict[str, float] = {muscle: 0.0 for muscle in self.muscle_groups}
        # Previous MVCP values
        self.previous_mvcp_data: Dict[str, float] = {muscle: 0.0 for muscle in self.muscle_groups}
        # Track which specific muscles have changed (pro status text)
        self.changed_muscles = set()

        # MQTT worker v separátním vlákně
        self.mqtt_worker = MQTTWorker()
        self.mqtt_thread = threading.Thread(target=self.setup_mqtt_worker, daemon=True)
        self.mqtt_thread.start()

        # Stavové příznaky
        self.is_mvc_setting = False
        self.wide_squat_active = False
        self.phase_key = "Phase"
        self.phase_raw = None
        self.phase_value = 0.0

        self.setup_ui()

    def setup_mqtt_worker(self):
        """Setup MQTT worker in separate thread"""
        self.mqtt_worker.data_received.connect(self.update_data)
        self.mqtt_worker.start_mqtt()

    def setup_ui(self):
        """Setup the user interface"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        # menší okraje, aby graf zabral víc výšky
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Title
        title_label = QLabel("Real-time MVCP Visualization")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        main_layout.addWidget(title_label)

        # Náš vlastní bar chart widget – expanduje na maximum
        self.bar_widget = BarChartWidget(self.muscle_groups, phase_key=self.phase_key, parent=self)
        self.bar_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main_layout.addWidget(self.bar_widget, stretch=1)

        # Control buttons
        button_layout = QHBoxLayout()

        self.reset_mvc_btn = QPushButton("Set MVC")
        self.reset_mvc_btn.clicked.connect(self.toggle_mvc)
        self.reset_mvc_btn.setMinimumHeight(40)
        self.reset_mvc_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff6b6b;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff5252;
            }
        """)
        button_layout.addWidget(self.reset_mvc_btn)

        self.wide_squat_btn = QPushButton("Set wide squat")
        self.wide_squat_btn.clicked.connect(self.toggle_wide_squat)
        self.wide_squat_btn.setMinimumHeight(40)
        self.wide_squat_btn.setStyleSheet("""
            QPushButton {
                background-color: #5c7cfa;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4263eb;
            }
        """)
        button_layout.addWidget(self.wide_squat_btn)

        main_layout.addLayout(button_layout)

        # Status label
        self.status_label = QLabel("Status: Waiting for MQTT data...")
        self.status_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.status_label)

        # Inicialní vykreslení
        self.update_chart()

    def update_chart(self):
        """Předá aktuální data do BarChartWidgetu."""
        self.bar_widget.set_data(self.muscle_groups, self.mvcp_data)

    def update_data(self, data):
        """Update MVCP data from MQTT message"""
        try:
            changed_muscles_this_update = set()

            if 'sensors' in data:
                for muscle_name, sensor_data in data['sensors'].items():
                    if muscle_name in self.mvcp_data and 'mvcp' in sensor_data:
                        new_value = self._to_float_or_none(sensor_data['mvcp'])
                        if new_value is not None and self.mvcp_data[muscle_name] != new_value:
                            self.mvcp_data[muscle_name] = new_value
                            changed_muscles_this_update.add(muscle_name)

            # Handle exercise phase for wide squat
            try:
                exdesc = data.get('exercise_description') if isinstance(data, dict) else None
                if isinstance(exdesc, dict) and 'phase' in exdesc:
                    p = float(exdesc['phase'])
                    # Map: 0 -> 100 (max), 50 -> 0, 100 -> 100
                    self.phase_value = max(0.0, min(100.0, 2.0 * abs(p - 50.0)))
                    if self.wide_squat_active:
                        if self.phase_key not in self.muscle_groups:
                            self._enable_phase_column()
                        self.mvcp_data[self.phase_key] = self.phase_value
                        changed_muscles_this_update.add(self.phase_key)
            except Exception:
                pass

            self.changed_muscles.update(changed_muscles_this_update)

            # Aktualizace status labelu
            if changed_muscles_this_update:
                changed_list = ", ".join(changed_muscles_this_update)
                if 'timestamp' in data:
                    timestamp = data['timestamp']
                    self.status_label.setText(f"Status: Updated {changed_list} - (Last: {timestamp})")
                else:
                    self.status_label.setText(f"Status: Updated {changed_list}")
            else:
                if 'timestamp' in data:
                    timestamp = data['timestamp']
                    self.status_label.setText(f"Status: Data received (no changes) - (Last: {timestamp})")

            # Posun dat do grafu (animace si řeší widget sám)
            self.update_chart()

            # Uložíme si předchozí hodnoty
            for m in changed_muscles_this_update:
                self.previous_mvcp_data[m] = self.mvcp_data[m]

        except Exception as e:
            print(f"Error updating data: {e}")
            self.status_label.setText(f"Status: Error processing data - {str(e)}")

    def toggle_mvc(self):
        """Toggle MVC set mode (publish mvc_start / mvc_stop)"""
        if not self.is_mvc_setting:
            command = {"cmd": "mvc_start"}
            self.mqtt_worker.send_command(command)
            self.is_mvc_setting = True
            self.reset_mvc_btn.setText("Stop MVC")
            self.status_label.setText("Status: MVC capture started")
        else:
            command = {"cmd": "mvc_stop"}
            self.mqtt_worker.send_command(command)
            self.is_mvc_setting = False
            self.reset_mvc_btn.setText("Set MVC")
            self.status_label.setText("Status: MVC capture stopped")

    def _to_float_or_none(self, x):
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if s == "" or s.upper() in ("N/A", "NA", "NULL", "NONE"):
            return None
        try:
            return float(s)
        except Exception:
            return None

    def _update_categories(self):
        """Jen interní – aktualizace pořadí kategorií pro chart widget."""
        self.update_chart()

    def _enable_phase_column(self):
        if self.phase_key not in self.muscle_groups:
            self.muscle_groups.append(self.phase_key)
            self.mvcp_data[self.phase_key] = getattr(self, "phase_value", 0.0)
            self.previous_mvcp_data[self.phase_key] = self.mvcp_data[self.phase_key]
            self._update_categories()

    def _disable_phase_column(self):
        if self.phase_key in self.muscle_groups:
            try:
                self.muscle_groups.remove(self.phase_key)
            except ValueError:
                pass

            if self.phase_key in self.mvcp_data:
                del self.mvcp_data[self.phase_key]
            if self.phase_key in self.previous_mvcp_data:
                del self.previous_mvcp_data[self.phase_key]

            self._update_categories()

    def toggle_wide_squat(self):
        """Toggle 'wide_squat' exercise set/unset and show/hide Phase column."""
        if not self.wide_squat_active:
            command = {"cmd": "set_exercise", "val": "wide_squat"}
            self.mqtt_worker.send_command(command)
            self.wide_squat_active = True
            self.wide_squat_btn.setText("Unset wide squat")
            self._enable_phase_column()
            self.status_label.setText("Status: Wide squat enabled")
        else:
            command = {"cmd": "set_exercise", "val": ""}
            self.mqtt_worker.send_command(command)
            self.wide_squat_active = False
            self.wide_squat_btn.setText("Set wide squat")
            self._disable_phase_column()
            self.status_label.setText("Status: Wide squat disabled")

    def closeEvent(self, event):
        """Handle application close"""
        self.mqtt_worker.stop_mqtt()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MVCPVisualizer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
